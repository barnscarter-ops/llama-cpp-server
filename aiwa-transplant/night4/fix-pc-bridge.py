from pathlib import Path

NEW = "100.124.41.115"
OLD = "100.124.216.11"


def rewrite(path: Path, keys: dict[str, str]) -> list[str]:
    changed = []
    lines = path.read_text().splitlines()
    out = []
    for ln in lines:
        hit = False
        for k, v in keys.items():
            if ln.startswith(k + "="):
                if ln != f"{k}={v}":
                    out.append(f"{k}={v}")
                    changed.append(f"{path}:{k}")
                else:
                    out.append(ln)
                hit = True
                break
        if not hit:
            out.append(ln)
    path.write_text("\n".join(out) + "\n")
    return changed


changed = []
changed += rewrite(
    Path("/home/hermes/.hermes/profiles/pc-sms/.env"),
    {
        "HERMES_PC_URL": f"http://{NEW}:8901",
        "OPENAI_BASE_URL": f"http://{NEW}:8080/v1",
    },
)
changed += rewrite(
    Path("/home/hermes/.hermes/.env"),
    {
        "DEADMAN_URL": f"http://{NEW}:8903/ping",
        "OPENAI_BASE_URL": f"http://{NEW}:8080/v1",
    },
)
print("changed", changed)
