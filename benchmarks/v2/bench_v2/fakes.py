"""A fake machine: processes, GPU, services, chat server, clock. Used by every test and by --dry-run.

Nothing here opens a socket or runs a subprocess. Performance and failure
behavior are scripted through `FakeWorld`.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass, field
from typing import Callable

from .backend import ChatResult, CmdResult, GpuSample

KV_FACTOR = {"f16": 1.0, "q8_0": 1.0625 / 2.0, "q4_0": 0.5625 / 2.0}


@dataclass
class SimModelSpec:
    weights_mib: float
    kv_kib_per_token_f16: float = 20.0
    compute_mib: float = 1000.0
    load_s: float = 40.0
    extra_mib: float = 0.0           # draft / mmproj when present


def default_perf(p: dict) -> dict:
    """pp/tg tokens per second as a function of the parsed launch params."""
    tg = 80.0 * (0.94 if p["kv"] == "q8_0" else 1.0) * (1.0 if p["fa"] else 0.8)
    pp = 2000.0 * (p["ub"] / 512.0) ** 0.25
    return {"pp": pp, "tg": tg}


@dataclass
class FakeWorld:
    vram_total_mib: float = 16380.0
    overhead_mib: float = 700.0
    models: dict = field(default_factory=dict)          # filename -> SimModelSpec
    perf_fn: Callable[[dict], dict] = default_perf
    noise: float = 0.005
    seed: int = 1
    temp_c: float = 55.0
    shared_mib: float = 0.0
    responder: Callable[[dict, dict], str] | None = None   # (request body, launch params) -> text
    tool_responder: Callable[[dict, dict], list] | None = None
    crash_when: Callable[[dict], str | None] | None = None  # params -> "oom" | "crash" | None
    power_alarm_after_requests: int | None = None
    services: dict = field(default_factory=dict)         # name -> bool (active)
    journal_listening: bool = True
    cmd_log: list = field(default_factory=list)
    requests: list = field(default_factory=list)         # (url, kind)
    request_count: int = 0
    free_disk_gb: float = 500.0
    ppl_fn: Callable[[dict], tuple[float, float]] | None = None  # params -> (ppl, kld)
    stop_fails: bool = False
    t: float = 1_800_000_000.0
    spawned: list = field(default_factory=list)
    resident: str = "Nemotron-3.5-Lightning-Q5_K_M.gguf"   # what the production service has loaded (R9700)
    swap_fail: set = field(default_factory=set)         # script basenames that exit non-zero
    uptimes: dict = field(default_factory=dict)         # pm2 name -> restart time (virtual seconds)
    status_garbage: bool = False                         # service status commands print unparseable text
    other_gpu_mib: float = 0.0


class FakeProc:
    _pid = 1000

    def __init__(self, world: FakeWorld, params: dict, ready_at: float, log: list[str], rc: int | None) -> None:
        FakeProc._pid += 1
        self.pid = FakeProc._pid
        self.world, self.params, self.ready_at, self._log, self.rc = world, params, ready_at, log, rc

    def poll(self) -> int | None:
        return self.rc

    def terminate(self) -> None:
        if self.rc is None:
            self.rc = 0

    def kill(self) -> None:
        if self.rc is None:
            self.rc = -9

    def wait(self, timeout: float) -> int | None:
        return self.rc

    def log_tail(self, n: int = 40) -> list[str]:
        return self._log[-n:]


def parse_launch(argv: list[str]) -> dict:
    def val(flag: str, default=None):
        for f in ((flag,) if isinstance(flag, str) else flag):
            if f in argv:
                return argv[argv.index(f) + 1]
        return default
    spec = val("--spec-type")
    return {
        "model": val("-m", ""), "port": int(val("--port", 0)), "ctx": int(val("-c", 0)),
        "kv": val("-ctk", "f16"), "kv_v": val("-ctv", "f16"), "fa": val("--flash-attn", "on") == "on",
        "b": int(val("-b", 2048)), "ub": int(val("-ub", 512)),
        "spec": "mtp" if spec else "off", "n_cpu_moe": int(val("--n-cpu-moe", 0)),
        "mmproj": "--mmproj" in argv, "reasoning_off": "--reasoning" in argv and val("--reasoning") == "off",
        "threads": int(val("-t", 0)), "argv": argv,
    }


class FakeBackend:
    def __init__(self, world: FakeWorld | None = None) -> None:
        self.w = world or FakeWorld()
        self.rng = random.Random(self.w.seed)
        self.current: FakeProc | None = None

    # -- clock --
    def now(self) -> float:
        return self.w.t

    def sleep(self, seconds: float) -> None:
        self.w.t += seconds

    # -- processes --
    def spawn(self, cmd: list[str], env, log_path: str) -> FakeProc:
        w = self.w
        params = parse_launch(cmd)
        name = params["model"].replace("\\", "/").split("/")[-1]
        spec = w.models.get(name)
        w.spawned.append(cmd)
        if spec is None:
            proc = FakeProc(w, params, 0, [f"error: failed to load model '{name}'"], 1)
            return proc
        verdict = w.crash_when(params) if w.crash_when else None
        kv_mib = spec.kv_kib_per_token_f16 * params["ctx"] * KV_FACTOR.get(params["kv"], 1.0) / 1024.0
        weights = spec.weights_mib
        if params["n_cpu_moe"]:
            weights = max(weights * 0.4, weights - params["n_cpu_moe"] * 470.0)
        used = weights + kv_mib + spec.compute_mib + w.overhead_mib + spec.extra_mib + w.other_gpu_mib
        log = ["build: 10505 (abc1234) with fake compiler"]
        if verdict == "crash":
            return FakeProc(w, params, 0, log + ["segfault in fake"], 139)
        if verdict == "oom" or used > w.vram_total_mib:
            log.append("ggml_backend_cuda_buffer_type_alloc_buffer: allocating 20000 MiB on device 0: "
                       "cudaMalloc failed: out of memory")
            return FakeProc(w, params, 0, log, 1)
        log += [f"CUDA0 model buffer size = {weights:.2f} MiB", f"CUDA0 KV buffer size = {kv_mib:.2f} MiB",
                f"CUDA0 compute buffer size = {spec.compute_mib:.2f} MiB", "CPU_Mapped model buffer size = 166.00 MiB",
                "main: server is listening on http://127.0.0.1"]
        proc = FakeProc(w, params, w.t + spec.load_s, log, None)
        proc.used_mib = used  # type: ignore[attr-defined]
        self.current = proc
        return proc

    def http_get(self, url: str, timeout: float = 5.0) -> tuple[int, str]:
        self.w.requests.append((url, "get"))
        p = self.current
        if p is None or p.rc is not None:
            return 0, "connection refused"
        return (200, '{"status":"ok"}') if self.w.t >= p.ready_at else (503, "loading")

    def chat(self, url: str, body: dict, timeout: float = 600.0, headers: dict | None = None) -> ChatResult:
        w = self.w
        w.requests.append((url, "chat"))
        w.request_count += 1
        p = self.current
        if p is None or p.rc is not None:
            return ChatResult(False, error="connection refused")
        spec = w.models[p.params["model"].replace("\\", "/").split("/")[-1]]
        perf = w.perf_fn({**p.params, "spec": p.params["spec"]})
        jitter = lambda: 1.0 + self.rng.uniform(-w.noise, w.noise)  # noqa: E731
        text_in = json.dumps(body.get("messages", ""))
        n_in = max(1, int(len(text_in) / 3.5))
        max_tokens = int(body.get("max_tokens") or 256)
        text = w.responder(body, p.params) if w.responder else "OK"
        n_out = min(max_tokens, max(1, int(len(text) / 3.5)) if w.responder else min(max_tokens, 200))
        calls = w.tool_responder(body, p.params) if (w.tool_responder and body.get("tools")) else []
        pp, tg = perf["pp"] * jitter(), perf["tg"] * jitter()
        ttft = n_in / pp + 1.0 / tg
        total = ttft + max(0, n_out - 1) / tg
        w.t += total
        return ChatResult(True, text=text, tool_calls=calls, prompt_tokens=n_in, completion_tokens=n_out,
                          ttft_s=ttft, total_s=total, finish_reason="stop", prompt_tps=pp, predicted_tps=tg)

    # -- commands --
    def run(self, cmd: list[str], timeout: float = 120.0, env=None) -> CmdResult:
        w = self.w
        w.cmd_log.append(list(cmd))
        joined = " ".join(cmd)
        if w.status_garbage and (("pm2 jlist" in joined) or (cmd and cmd[0] == "systemctl" and cmd[1] == "is-active")):
            return CmdResult(0, "[PM2] banner without any json\nwhat?\n")
        base = cmd[0].replace("\\", "/").split("/")[-1] if cmd else ""
        if base in ("swap-qwen-consult.sh", "swap-nemo-clerk.sh"):
            if base in w.swap_fail:
                return CmdResult(1, "", "swap failed")
            w.resident = "Qwen3.8-27B-Q6_K.gguf" if "qwen" in base else "Nemotron-3.5-Lightning-Q5_K_M.gguf"
            return CmdResult(0)
        if cmd and cmd[0] == "pgrep":
            return CmdResult(0, f"4242 /opt/llama/llama-server -m /opt/llama/models/{w.resident} --port 8080\n")
        if cmd and cmd[0] == "systemctl":
            action, name = cmd[1], cmd[2]
            if action == "is-active":
                return CmdResult(0 if w.services.get(name) else 3, "active\n" if w.services.get(name) else "inactive\n")
            if action == "stop":
                if w.stop_fails:
                    return CmdResult(1, "", "stop refused")
                w.services[name] = False
                return CmdResult(0)
            if action == "start":
                w.services[name] = True
                return CmdResult(0)
        if cmd and cmd[0] == "journalctl":
            name = cmd[2]
            ok = w.services.get(name) and w.journal_listening
            return CmdResult(0, "main: server is listening on :8080\n" if ok else "loading...\n")
        if "pm2 jlist" in joined:
            apps = [{"name": n, "pm2_env": {"status": "online" if a else "stopped", "pm_uptime": w.uptimes.get(n, 0.0) * 1000.0}}
                    for n, a in w.services.items()]
            return CmdResult(0, "noise line\n" + json.dumps(apps))
        m = re.search(r"pm2 (stop|restart) (\S+)", joined)
        if m:
            if w.stop_fails and m[1] == "stop":
                return CmdResult(1, "", "denied")
            w.services[m[2]] = m[1] == "restart"
            if m[1] == "restart":
                w.uptimes[m[2]] = w.t
            return CmdResult(0)
        if "llama-perplexity" in cmd[0]:
            p = parse_launch(cmd)
            if w.ppl_fn is None:
                return CmdResult(0, "Final estimate: PPL = 6.0000 +/- 0.05\nMean    KLD:   0.000000 +-  0.000000\n")
            ppl, kld = w.ppl_fn(p)
            return CmdResult(0, f"Final estimate: PPL = {ppl:.4f} +/- 0.05\nMean    KLD:   {kld:.6f} +-  0.000100\n")
        return CmdResult(0, "")

    # -- gpu / system --
    def gpu_sample(self) -> GpuSample | None:
        w = self.w
        used = w.other_gpu_mib + 300.0
        if self.current is not None and self.current.rc is None:
            used = getattr(self.current, "used_mib", used)
        s = GpuSample(used, w.vram_total_mib, w.temp_c, 120.0, 2400.0, w.shared_mib)
        return s

    def power_alarm(self) -> str | None:
        n = self.w.power_alarm_after_requests
        if n is not None and self.w.request_count >= n:
            return "ups.status: OB LB"
        return None

    def free_disk_gb(self, path: str) -> float:
        return self.w.free_disk_gb

    def sha256(self, path: str) -> str:
        return hashlib.sha256(path.encode()).hexdigest()

    def file_exists(self, path: str) -> bool:
        return True

    def read_text(self, path: str) -> str:
        return ""


def world_for_lane(lane, cands, **overrides) -> FakeWorld:
    """A FakeWorld shaped like a real lane: its VRAM, its services (all active) and one sim model per candidate file."""
    w = FakeWorld(vram_total_mib=lane.raw["vram_gib"] * 1024.0, overhead_mib=lane.raw.get("overhead_gib", 0.0) * 1024.0, **overrides)
    for c in cands:
        if c.lane == lane.name and c.get("file"):
            w.models[c["file"]] = SimModelSpec(weights_mib=c["weights_gib"] * 1024.0,
                                               kv_kib_per_token_f16=c["kv_kib_per_token_f16"],
                                               compute_mib=(c.get("compute_gib") or lane.raw.get("default_compute_gib", 1.0)) * 1024.0,
                                               extra_mib=((c.get("draft") or {}).get("gib", 0.0) + (c.get("mmproj") or {}).get("gib", 0.0)) * 1024.0)
    for svc in lane.raw["safety"]["services"]:
        w.services[svc["name"]] = True
    return w


def oracle_hooks(items: list[dict]):
    """(responder, tool_responder) that answer frozen items correctly where a reference is cheap (tool calls, exact answers).

    Keys on the exact message list, so a fake 'model' only gets credit for items it was literally shown.
    """
    by_key = {json.dumps(i["messages"], sort_keys=True): i for i in items if i.get("messages")}

    def tool_responder(body: dict, params: dict) -> list:
        it = by_key.get(json.dumps(body.get("messages"), sort_keys=True))
        if not it or it.get("scorer", {}).get("type") != "tool":
            return []
        schemas = {t["function"]["name"]: t["function"]["parameters"] for t in it["tools"]}
        out = []
        for n, c in enumerate(it["scorer"]["calls"]):
            args = dict(c["args"])
            for req in schemas.get(c["name"], {}).get("required", []):  # the scorer leaves free-form required args open
                args.setdefault(req, "*")
            out.append({"id": f"c{n}", "name": c["name"], "arguments": json.dumps(args)})
        return out

    def responder(body: dict, params: dict) -> str:
        it = by_key.get(json.dumps(body.get("messages"), sort_keys=True))
        sc = (it or {}).get("scorer", {})
        if sc.get("type") == "tool":
            return sc.get("answer_contains", "") or "done"
        if sc.get("type") == "exact":
            return f"ANSWER: {sc['answers'][0]}"
        return "OK"

    return responder, tool_responder
