from pathlib import Path

NEW = "100.124.41.115"


def rewrite(path: Path, keys: dict[str, str]) -> list[str]:
    changed = []
    lines = path.read_text().splitlines()
    out = []
    for ln in lines:
        hit = False
        for k, v in keys.items():
            if ln.startswith(k + "="):
                nv = f"{k}={v}"
                if ln != nv:
                    changed.append(f"{path}:{k}")
                out.append(nv)
                hit = True
                break
        if not hit:
            out.append(ln.replace("100.124.216.11", NEW))
            if "100.124.216.11" in ln:
                changed.append(f"{path}:replaced-old-ip-in-other-line")
    path.write_text("\n".join(out) + "\n")
    return changed


keys = {
    "HERMES_PC_URL": f"http://{NEW}:8901",
    "PC_HOST": NEW,
    "OPENAI_BASE_URL": f"http://{NEW}:8080/v1",
    "DEADMAN_URL": f"http://{NEW}:8903/ping",
    "PC_ACTIONS_URL": f"http://{NEW}:8901",
}

changed = []
changed += rewrite(Path("/home/hermes/.hermes/.env"), keys)
changed += rewrite(Path("/home/hermes/.hermes/profiles/customer-sms/.env"), keys)
print("changed", changed)
