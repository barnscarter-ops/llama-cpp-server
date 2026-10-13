"""One bench llama-server on its own port: launch, classify failures, stop."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .backend import Backend, Proc
from .config import Candidate, LaneConfig
from .fitcheck import parse_buffers

OOM_PAT = re.compile(r"out of memory|cudaMalloc failed|failed to allocate|ErrorOutOfDeviceMemory|"
                     r"failed to create context|unable to allocate|hipMalloc failed|alloc.*failed", re.I)
VERSION_PAT = re.compile(r"build:?\s*(\d+)\s*\(([0-9a-f]+)\)|version:\s*(\d+)\s*\(([0-9a-f]+)\)", re.I)


@dataclass
class ServerConfig:
    """The levers. Everything else is derived from the candidate and lane."""
    ctx: int = 131072
    kv: str = "f16"
    fa: bool = True
    b: int = 2048
    ub: int = 512
    spec: str = "off"
    n_cpu_moe: int = 0
    threads: int | None = None
    reasoning: str | None = None  # None = candidate's production mode
    mmproj: bool = True
    np: int = 1

    def key(self) -> str:
        return (f"ctx{self.ctx}-kv{self.kv}-fa{int(self.fa)}-b{self.b}-ub{self.ub}-spec{self.spec}"
                f"-moe{self.n_cpu_moe}-t{self.threads}-r{self.reasoning}-mm{int(self.mmproj)}")

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def build_argv(lane: LaneConfig, cand: Candidate, cfg: ServerConfig, server_bin: str, models_dir: str) -> list[str]:
    sep = "\\" if ("\\" in models_dir and "/" not in models_dir) else "/"
    path = lambda f: f"{models_dir.rstrip('/' + chr(92))}{sep}{f}"  # noqa: E731
    mode = cand["reasoning_modes"][cfg.reasoning or cand["production_reasoning"]]
    ngl = "999" if lane.raw["gpu_kind"] != "nvidia" else "99"
    argv = [server_bin, "-m", path(cand["file"]), "--host", "127.0.0.1", "--port", str(lane.port),
            "-c", str(cfg.ctx), "-ngl", ngl, "--flash-attn", "on" if cfg.fa else "off",
            "-ctk", cfg.kv, "-ctv", cfg.kv, "-b", str(cfg.b), "-ub", str(cfg.ub),
            "-np", str(cfg.np), "--alias", cand.id]
    argv += list(cand.get("base_flags", []))
    argv += list(mode.get("flags", []))
    spec_flags = cand["spec_options"].get(cfg.spec)
    if spec_flags is None:
        raise ValueError(f"{cand.id}: no spec option {cfg.spec!r}")
    draft = cand.get("draft")
    spec_flags = [f.replace("{draft_path}", path(draft["file"]) if draft else "") for f in spec_flags]
    argv += spec_flags
    mm = cand.get("mmproj")
    conflicts = (cand.get("spec_conflicts") or {}).get(cfg.spec, [])
    if mm and cfg.mmproj and "mmproj" not in conflicts:
        argv += ["--mmproj", path(mm["file"])]
    if cfg.n_cpu_moe:
        argv += ["--n-cpu-moe", str(cfg.n_cpu_moe)]
    if cfg.threads:
        argv += ["-t", str(cfg.threads)]
    return argv


@dataclass
class LaunchResult:
    status: str                      # ok | oom | crash | timeout
    cold_s: float = 0.0
    log_tail: list = field(default_factory=list)
    buffers: dict = field(default_factory=dict)
    version: str | None = None
    proc: Proc | None = None


class BenchServer:
    def __init__(self, backend: Backend, lane: LaneConfig, health_timeout_s: float = 600.0) -> None:
        self.b = backend
        self.lane = lane
        self.health_timeout_s = health_timeout_s
        self.proc: Proc | None = None

    def launch(self, argv: list[str], log_path: str, env: dict | None = None) -> LaunchResult:
        if self.proc is not None:
            raise RuntimeError("one model server per machine: stop the running one first")
        t0 = self.b.now()
        self.proc = proc = self.b.spawn(argv, env, log_path)
        url = f"{self.lane.base_url}/health"
        while True:
            rc = proc.poll()
            if rc is not None:
                tail = proc.log_tail(200)
                status = "oom" if any(OOM_PAT.search(line) for line in tail) else "crash"
                self.proc = None
                return LaunchResult(status, self.b.now() - t0, proc.log_tail(40), parse_buffers(tail))
            code, _ = self.b.http_get(url, timeout=3.0)
            if code == 200:
                tail = proc.log_tail(400)
                return LaunchResult("ok", self.b.now() - t0, tail[-40:], parse_buffers(tail),
                                    _version(tail), proc)
            if self.b.now() - t0 > self.health_timeout_s:
                tail = proc.log_tail(40)
                self.stop()
                return LaunchResult("timeout", self.b.now() - t0, tail, parse_buffers(tail))
            self.b.sleep(2.0)

    def stop(self, release_timeout_s: float = 90.0, idle_baseline_mib: float | None = None) -> bool:
        """Terminate, then wait for VRAM to drop back near the pre-launch level."""
        proc, self.proc = self.proc, None
        if proc is not None:
            proc.terminate()
            if proc.wait(15) is None:
                proc.kill()
                proc.wait(15)
        if idle_baseline_mib is None:
            return True
        waited = 0.0
        while waited <= release_timeout_s:
            s = self.b.gpu_sample()
            if s is None or s.used_mib <= idle_baseline_mib + 500:
                return True
            self.b.sleep(3.0)
            waited += 3.0
        return False

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None


def _version(lines: list[str]) -> str | None:
    for line in lines:
        m = VERSION_PAT.search(line)
        if m:
            return f"b{m[1] or m[3]} ({m[2] or m[4]})"
    return None
