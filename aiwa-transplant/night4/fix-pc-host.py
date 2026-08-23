from pathlib import Path

p = Path("/home/hermes/.hermes/.env")
nl = []
for ln in p.read_text().splitlines():
    if ln.startswith("HERMES_PC_URL="):
        nl.append("HERMES_PC_URL=http://100.124.41.115:8901")
    elif ln.startswith("PC_HOST="):
        nl.append("PC_HOST=100.124.41.115")
    else:
        nl.append(ln)
p.write_text("\n".join(nl) + "\n")
print("rewrote HERMES_PC_URL and PC_HOST")
