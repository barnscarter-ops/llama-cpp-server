"""The only door to the outside world: processes, HTTP, GPU, clock, files.

Everything else in the harness talks to a `Backend`, so tests swap in a fake
(`fakes.FakeBackend`) and nothing real is touched.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from .errors import AbortRun, ForbiddenTarget

# Guardian front door and its llama-server; a bench request must never reach them.
FORBIDDEN_PORTS = frozenset({8080, 8081})
FORBIDDEN_HOSTS = frozenset({"192.168.1.240"})


@dataclass
class CmdResult:
    rc: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class ChatResult:
    ok: bool
    text: str = ""
    reasoning_text: str = ""
    tool_calls: list = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    ttft_s: float = 0.0
    total_s: float = 0.0
    finish_reason: str = ""
    error: str | None = None
    prompt_tps: float | None = None  # llama-server `timings`, when present
    predicted_tps: float | None = None


@dataclass
class GpuSample:
    used_mib: float
    total_mib: float
    temp_c: float = 0.0
    power_w: float = 0.0
    clock_mhz: float = 0.0
    shared_mib: float = 0.0  # shared/system memory in use by the GPU (Windows spill)


class Proc(Protocol):
    pid: int

    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float) -> int | None: ...
    def log_tail(self, n: int = 40) -> list[str]: ...


class Backend(Protocol):
    def run(self, cmd: list[str], timeout: float = 120.0, env: dict | None = None) -> CmdResult: ...
    def spawn(self, cmd: list[str], env: dict | None, log_path: str) -> Proc: ...
    def http_get(self, url: str, timeout: float = 5.0) -> tuple[int, str]: ...
    def chat(self, url: str, body: dict, timeout: float = 600.0, headers: dict | None = None) -> ChatResult: ...
    def gpu_sample(self) -> GpuSample | None: ...
    def power_alarm(self) -> str | None: ...
    def free_disk_gb(self, path: str) -> float: ...
    def sha256(self, path: str) -> str: ...
    def file_exists(self, path: str) -> bool: ...
    def read_text(self, path: str) -> str: ...
    def sleep(self, seconds: float) -> None: ...
    def now(self) -> float: ...


def check_target(url: str, allowed_ports: set[int]) -> None:
    """Refuse any URL that is not one of this lane's own bench ports."""
    parts = urllib.parse.urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    host = parts.hostname or ""
    if port in FORBIDDEN_PORTS and port not in allowed_ports:
        raise ForbiddenTarget(url)
    if host in FORBIDDEN_HOSTS and port not in allowed_ports:
        raise ForbiddenTarget(url)
    if allowed_ports and port not in allowed_ports:
        raise ForbiddenTarget(url)


class GuardedBackend:
    """Wraps a Backend and aborts the run if a request leaves the bench port."""

    def __init__(self, inner: Backend, allowed_ports: set[int]) -> None:
        self._inner = inner
        self.allowed_ports = set(allowed_ports)
        self.violations: list[str] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _check(self, url: str) -> None:
        try:
            check_target(url, self.allowed_ports)
        except ForbiddenTarget:
            self.violations.append(url)
            raise

    def http_get(self, url: str, timeout: float = 5.0) -> tuple[int, str]:
        self._check(url)
        return self._inner.http_get(url, timeout)

    def chat(self, url: str, body: dict, timeout: float = 600.0, headers: dict | None = None) -> ChatResult:
        self._check(url)
        return self._inner.chat(url, body, timeout, headers)


# ── real implementation ─────────────────────────────────────────────────────


class _RealProc:
    def __init__(self, popen: subprocess.Popen, log_path: str, log_file) -> None:
        self._p = popen
        self._log_path = log_path
        self._log_file = log_file
        self.pid = popen.pid

    def poll(self) -> int | None:
        return self._p.poll()

    def terminate(self) -> None:
        self._p.terminate()

    def kill(self) -> None:
        self._p.kill()

    def wait(self, timeout: float) -> int | None:
        try:
            rc = self._p.wait(timeout)
        except subprocess.TimeoutExpired:
            return None
        self._log_file.close()
        return rc

    def log_tail(self, n: int = 40) -> list[str]:
        try:
            with open(self._log_path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read().splitlines()[-n:]
        except OSError:
            return []


class SystemBackend:
    """Real subprocess/HTTP/GPU access. Never constructed by tests."""

    def __init__(self, gpu_kind: str = "nvidia", power_cmd: list[str] | None = None,
                 power_alarm_regex: str | None = None) -> None:
        self.gpu_kind = gpu_kind
        self.power_cmd = power_cmd
        self.power_alarm_regex = power_alarm_regex

    def run(self, cmd: list[str], timeout: float = 120.0, env: dict | None = None) -> CmdResult:
        merged = {**os.environ, **env} if env else None
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=merged)
        except FileNotFoundError as exc:
            return CmdResult(127, "", str(exc))
        except subprocess.TimeoutExpired as exc:
            return CmdResult(124, exc.stdout or "" if isinstance(exc.stdout, str) else "", "timeout")
        return CmdResult(p.returncode, p.stdout, p.stderr)

    def spawn(self, cmd: list[str], env: dict | None, log_path: str) -> _RealProc:
        os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
        fh = open(log_path, "w", encoding="utf-8")
        merged = {**os.environ, **env} if env else None
        popen = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, env=merged)
        return _RealProc(popen, log_path, fh)

    def http_get(self, url: str, timeout: float = 5.0) -> tuple[int, str]:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")
        except Exception as exc:  # connection refused while loading is normal
            return 0, str(exc)

    def chat(self, url: str, body: dict, timeout: float = 600.0, headers: dict | None = None) -> ChatResult:
        """Streaming chat completion; TTFT is the first content/reasoning/tool delta."""
        body = {**body, "stream": True, "stream_options": {"include_usage": True}}
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **(headers or {})})
        t0 = time.monotonic()
        ttft = 0.0
        text: list[str] = []
        reasoning: list[str] = []
        calls: dict[int, dict] = {}
        usage: dict = {}
        timings: dict = {}
        finish = ""
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                for raw in r:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        obj = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("usage"):
                        usage = obj["usage"]
                    if obj.get("timings"):
                        timings = obj["timings"]
                    for ch in obj.get("choices") or []:
                        delta = ch.get("delta") or {}
                        got = False
                        if delta.get("content"):
                            text.append(delta["content"]); got = True
                        if delta.get("reasoning_content"):
                            reasoning.append(delta["reasoning_content"]); got = True
                        for tc in delta.get("tool_calls") or []:
                            slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                            fn = tc.get("function") or {}
                            slot["id"] = tc.get("id") or slot["id"]
                            slot["name"] += fn.get("name") or ""
                            slot["arguments"] += fn.get("arguments") or ""
                            got = True
                        if got and not ttft:
                            ttft = time.monotonic() - t0
                        if ch.get("finish_reason"):
                            finish = ch["finish_reason"]
        except Exception as exc:
            return ChatResult(ok=False, error=f"{type(exc).__name__}: {exc}",
                              total_s=time.monotonic() - t0)
        total = time.monotonic() - t0
        return ChatResult(
            ok=True, text="".join(text), reasoning_text="".join(reasoning),
            tool_calls=[calls[k] for k in sorted(calls)],
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            ttft_s=ttft or total, total_s=total, finish_reason=finish,
            prompt_tps=timings.get("prompt_per_second"),
            predicted_tps=timings.get("predicted_per_second"),
        )

    def gpu_sample(self) -> GpuSample | None:
        if self.gpu_kind == "nvidia":
            r = self.run(["nvidia-smi", "--query-gpu=memory.used,memory.total,temperature.gpu,power.draw,clocks.sm",
                          "--format=csv,noheader,nounits"], timeout=10)
            if r.rc != 0 or not r.stdout.strip():
                return None
            try:
                a = [float(x) for x in r.stdout.strip().splitlines()[0].split(",")]
            except ValueError:
                return None
            return GpuSample(a[0], a[1], a[2], a[3], a[4])
        if self.gpu_kind == "amdgpu_sysfs":
            return self._amd_sample()
        return None

    def _amd_sample(self) -> GpuSample | None:
        # One discrete card expected; pick the card with the biggest VRAM.
        best = None
        for dev in glob.glob("/sys/class/drm/card[0-9]/device"):
            try:
                total = int(open(os.path.join(dev, "mem_info_vram_total")).read()) / 1048576
                used = int(open(os.path.join(dev, "mem_info_vram_used")).read()) / 1048576
            except (OSError, ValueError):
                continue
            temp = power = 0.0
            for hw in glob.glob(os.path.join(dev, "hwmon", "hwmon*")):
                try:
                    temp = int(open(os.path.join(hw, "temp1_input")).read()) / 1000.0
                except (OSError, ValueError):
                    pass
                for name in ("power1_average", "power1_input"):
                    try:
                        power = int(open(os.path.join(hw, name)).read()) / 1e6
                        break
                    except (OSError, ValueError):
                        pass
            if best is None or total > best.total_mib:
                best = GpuSample(used, total, temp, power)
        return best

    def power_alarm(self) -> str | None:
        """Returns a description when the UPS reports an alarm, else None (also None if unmonitored)."""
        if not self.power_cmd or not self.power_alarm_regex:
            return None
        import re
        r = self.run(self.power_cmd, timeout=10)
        if r.rc == 0 and re.search(self.power_alarm_regex, r.stdout):
            return r.stdout.strip()[:200]
        return None

    def free_disk_gb(self, path: str) -> float:
        return shutil.disk_usage(path).free / 1e9

    def sha256(self, path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 24), b""):
                h.update(chunk)
        return h.hexdigest()

    def file_exists(self, path: str) -> bool:
        return os.path.exists(path)

    def read_text(self, path: str) -> str:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def now(self) -> float:
        return time.time()
