"""Deterministic scorers. A scorer returns (passed, detail). Rubric items are never auto-scored."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile

try:  # POSIX only; the Workbench (Windows) never executes model code
    import resource
except ImportError:
    resource = None

THINK = re.compile(r"<think>.*?</think>", re.S)
FENCE = re.compile(r"```[a-zA-Z0-9_+-]*\n(.*?)```", re.S)
ANSWER = re.compile(r"ANSWER:\s*(.+)", re.I)
CODE_TIMEOUT_S = 10


def clean(text: str) -> str:
    """Drop inline reasoning blocks; the answer is whatever follows."""
    text = THINK.sub("", text)
    if "</think>" in text:  # opening tag was in the prompt template
        text = text.split("</think>", 1)[1]
    return text.strip()


def last_fence(text: str) -> str | None:
    blocks = FENCE.findall(text)
    return blocks[-1] if blocks else None


def extract_code(text: str) -> str:
    text = clean(text)
    return last_fence(text) or text


def extract_answer(text: str) -> str | None:
    found = ANSWER.findall(clean(text))
    return found[-1].strip().strip("`*. ") if found else None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def score_exact(text: str, spec: dict) -> tuple[bool, str]:
    ans = extract_answer(text)
    if ans is None:
        return False, "no ANSWER: line"
    want = [str(a) for a in spec["answers"]]
    if spec.get("numeric"):
        try:
            return (any(abs(float(ans.replace(",", "")) - float(w)) < 1e-6 for w in want), f"got {ans}")
        except ValueError:
            return False, f"not numeric: {ans}"
    return (any(_norm(ans) == _norm(w) for w in want), f"got {ans}")


def score_exact_text(text: str, spec: dict) -> tuple[bool, str]:
    got = last_fence(clean(text))
    got = (got if got is not None else clean(text)).strip("\n")
    want = spec["expected"].strip("\n")
    if spec.get("strip_trailing_ws", True):
        got = "\n".join(l.rstrip() for l in got.splitlines())
        want = "\n".join(l.rstrip() for l in want.splitlines())
    return got.strip() == want.strip(), "match" if got.strip() == want.strip() else "text differs"


def score_json(text: str, spec: dict) -> tuple[bool, str]:
    body = last_fence(clean(text)) or clean(text)
    start = min([i for i in (body.find("{"), body.find("[")) if i >= 0], default=-1)
    if start < 0:
        return False, "no JSON"
    try:
        got, _ = json.JSONDecoder().raw_decode(body[start:])
    except json.JSONDecodeError as exc:
        return False, f"invalid JSON: {exc}"
    return got == spec["expected"], "match" if got == spec["expected"] else "JSON differs"


def score_needle(text: str, spec: dict) -> tuple[bool, str]:
    body = clean(text)
    missing = [n for n in spec["needles"] if n not in body]
    return not missing, f"missing {missing}" if missing else "all found"


MEM_LIMIT_BYTES = 2 * 1024 ** 3
_RUNNER: dict = {"drop_user": None, "sandbox_cmd": None}
WINDOWS_ENV_KEYS = ("SYSTEMROOT", "SYSTEMDRIVE", "PATH", "PATHEXT", "COMSPEC", "TEMP", "TMP", "WINDIR")


def configure_runner(drop_user: str | None = None, sandbox_cmd: list[str] | None = None) -> None:
    """How model-generated code is run: as an unprivileged user (needs root) and/or inside a sandbox command prefix."""
    _RUNNER["drop_user"], _RUNNER["sandbox_cmd"] = drop_user, sandbox_cmd


def can_execute() -> tuple[bool, str]:
    """Whether this process may run model-generated code at all. Never as the person who owns the machine."""
    if _RUNNER["sandbox_cmd"]:
        return True, "sandbox command"
    if os.name != "posix":
        return False, "not POSIX: no way to drop privileges"
    if _RUNNER["drop_user"]:
        try:
            import pwd
            pwd.getpwnam(_RUNNER["drop_user"])
        except KeyError:
            return False, f"user {_RUNNER['drop_user']!r} does not exist"
        if os.geteuid() != 0:
            return False, f"cannot drop to {_RUNNER['drop_user']!r} without root"
        return True, f"runs as {_RUNNER['drop_user']}"
    return False, "no unprivileged user or sandbox configured"


def child_env(tmp: str, windows: bool, environ=None) -> dict:
    """Scrubbed environment for the child. Windows Python will not start without SYSTEMROOT and friends."""
    environ = os.environ if environ is None else environ
    if windows:
        env = {k: environ[k] for k in WINDOWS_ENV_KEYS if k in environ}
    else:
        env = {"PATH": environ.get("PATH", ""), "HOME": tmp}
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _limits(uid_gid: tuple[int, int] | None) -> "callable":
    def run() -> None:  # POSIX only; runs in the child before exec
        resource.setrlimit(resource.RLIMIT_CPU, (CODE_TIMEOUT_S, CODE_TIMEOUT_S))
        resource.setrlimit(resource.RLIMIT_AS, (MEM_LIMIT_BYTES, MEM_LIMIT_BYTES))
        resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 ** 2, 16 * 1024 ** 2))
        if uid_gid:
            os.setgroups([])
            os.setgid(uid_gid[1])
            os.setuid(uid_gid[0])
    return run


def score_python(text: str, spec: dict) -> tuple[bool, str]:
    """Run the model's code plus tests in a child interpreter: own process group, CPU/memory/file-size limits,
    scrubbed environment, temp cwd, hard timeout that kills the whole group. Refuses unless `can_execute()`:
    the child runs as an unprivileged user or inside the configured sandbox command, never as the machine's owner.

    Still not a full OS sandbox: filesystem and network are reachable as that user unless the sandbox command
    (for example a throwaway container with --network=none) removes them."""
    ok_run, why_not = can_execute()
    if not ok_run:
        raise RuntimeError(f"refusing to execute model-generated code: {why_not}")
    code = extract_code(text)
    program = code + "\n\n" + spec["tests"] + "\n"
    posix = os.name == "posix"
    sandbox = _RUNNER["sandbox_cmd"]
    uid_gid = None
    if posix and _RUNNER["drop_user"] and not sandbox:
        import pwd
        pw = pwd.getpwnam(_RUNNER["drop_user"])
        uid_gid = (pw.pw_uid, pw.pw_gid)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "case.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(program)
        if uid_gid:
            os.chmod(tmp, 0o755)
            os.chmod(path, 0o644)
        env = child_env(tmp, os.name == "nt")
        if sandbox:
            argv, stdin_data = [*sandbox, "python3", "-I", "-"], program
        else:
            argv, stdin_data = [sys.executable, "-I", path], None
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE if stdin_data is not None else None, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, cwd=tmp, env=env, start_new_session=posix,
                                preexec_fn=_limits(uid_gid) if (posix and not sandbox) else None)
        try:
            out, err = proc.communicate(stdin_data, timeout=CODE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            _kill_group(proc, posix)
            proc.communicate()
            return False, "timeout"
        _kill_group(proc, posix, quiet=True)  # leftovers from a finished child (forked helpers)
        p = subprocess.CompletedProcess(proc.args, proc.returncode, out, err)
    if p.returncode == 0:
        return True, "tests passed"
    return False, (p.stderr.strip().splitlines() or ["failed"])[-1][:160]


def _kill_group(proc, posix: bool, quiet: bool = False) -> None:
    if posix:
        import signal
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    elif not quiet:
        proc.kill()


# -- IFEval-style programmatic constraints --
def _check(c: dict, body: str) -> bool:
    t = c["type"]
    words = re.findall(r"\b\w+\b", body)
    if t == "min_words":
        return len(words) >= c["n"]
    if t == "max_words":
        return len(words) <= c["n"]
    if t == "no_commas":
        return "," not in body
    if t == "all_lowercase":
        return body == body.lower()
    if t == "all_uppercase":
        return body == body.upper()
    if t == "keywords":
        return all(k.lower() in body.lower() for k in c["words"])
    if t == "forbidden":
        return not any(re.search(rf"\b{re.escape(w)}\b", body, re.I) for w in c["words"])
    if t == "bullets":
        return len(re.findall(r"^\s*[-*] ", body, re.M)) == c["n"]
    if t == "paragraphs":
        return len([p for p in re.split(r"\n\s*\n", body.strip()) if p.strip()]) == c["n"]
    if t == "ends_with":
        return body.strip().endswith(c["text"])
    if t == "starts_with":
        return body.strip().startswith(c["text"])
    if t == "json_only":
        try:
            json.loads(last_fence(body) or body)
            return True
        except json.JSONDecodeError:
            return False
    if t == "title_brackets":
        return bool(re.search(r"<<[^>\n]+>>", body))
    if t == "n_sentences":
        return len([s for s in re.split(r"(?<=[.!?])\s+", body.strip()) if s]) == c["n"]
    if t == "contains_n_placeholders":
        return len(re.findall(r"\[[^\]\n]+\]", body)) >= c["n"]
    raise ValueError(f"unknown constraint {t}")


def score_ifeval(text: str, spec: dict) -> tuple[bool, str]:
    body = clean(text)
    failed = [c["type"] for c in spec["constraints"] if not _check(c, body)]
    return not failed, "all constraints met" if not failed else f"failed {failed}"


SCORERS = {"exact": score_exact, "exact_text": score_exact_text, "json": score_json,
           "needle": score_needle, "python": score_python, "ifeval": score_ifeval}
