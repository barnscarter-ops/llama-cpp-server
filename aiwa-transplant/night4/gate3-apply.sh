#!/bin/bash
# Gate 3.2 apply extracted host-state onto live aiwa. No /etc/pve, no /etc/network.
set -x
exec > /root/gate3-apply.log 2>&1
SRC=/mnt/samsung-stage/host-restore

rsync -a "$SRC/root/" /root/
rsync -a "$SRC/opt/" /opt/
rsync -a "$SRC/home/" /home/
rsync -a "$SRC/usr/local/bin/" /usr/local/bin/
mkdir -p /var/lib/docker/volumes /var/lib/samba /var/lib/tailscale /var/spool/cron/crontabs
rsync -a "$SRC/var/lib/docker/volumes/" /var/lib/docker/volumes/
rsync -a "$SRC/var/lib/samba/" /var/lib/samba/
rsync -a "$SRC/var/lib/tailscale/" /var/lib/tailscale/
rsync -a "$SRC/var/spool/cron/" /var/spool/cron/

# users/groups with original ids
groupadd -g 1000 mavshare 2>/dev/null || true
groupadd -g 1001 hermes 2>/dev/null || true
groupadd -g 989 node_exporter 2>/dev/null || true
groupadd -g 986 syncthing 2>/dev/null || true
id mavshare >/dev/null 2>&1 || useradd -u 1000 -g 1000 -d /home/mavshare -s /usr/sbin/nologin mavshare
id hermes >/dev/null 2>&1 || useradd -u 1001 -g 1001 -d /home/hermes -s /bin/bash hermes
id node_exporter >/dev/null 2>&1 || useradd -u 999 -g 989 -d /home/node_exporter -s /bin/false node_exporter
id syncthing >/dev/null 2>&1 || useradd -u 997 -g 986 -d /home/syncthing -s /bin/false syncthing
mkdir -p /home/mavshare
chown -R hermes:hermes /home/hermes
chown -R syncthing:syncthing /home/syncthing
chown -R mavshare:mavshare /home/mavshare || true

# restore password hashes for those users
python3 - << 'PY'
from pathlib import Path
src = Path("/mnt/samsung-stage/host-restore/etc/shadow")
want = {"mavshare", "hermes", "node_exporter", "syncthing"}
hashes = {}
for line in src.read_text().splitlines():
    name = line.split(":", 1)[0]
    if name in want:
        hashes[name] = line
shadow = Path("/etc/shadow")
lines = shadow.read_text().splitlines()
out = []
seen = set()
for line in lines:
    name = line.split(":", 1)[0]
    if name in hashes:
        out.append(hashes[name])
        seen.add(name)
    else:
        out.append(line)
for name, line in hashes.items():
    if name not in seen:
        out.append(line)
shadow.write_text("\n".join(out) + "\n")
print("shadow_merged", sorted(hashes))
PY

install -d /etc/systemd/system
cp -a "$SRC/etc/systemd/system/." /etc/systemd/system/
# never install a host llama unit even if one appears later
rm -f /etc/systemd/system/llama-server.service

python3 - << 'PY'
from pathlib import Path
p = Path("/mnt/samsung-stage/host-restore/etc/samba/smb.conf")
text = p.read_text()
out = []
skip = False
for line in text.splitlines(True):
    if line.startswith("[proxmox-root]"):
        skip = True
        continue
    if skip and line.startswith("[") and not line.startswith("[proxmox-root]"):
        skip = False
    if not skip:
        out.append(line)
Path("/etc/samba/smb.conf").write_text("".join(out))
print("smb.conf written; proxmox-root stripped")
PY

# PC_HOST re-point
python3 - << 'PY'
from pathlib import Path
p = Path("/etc/systemd/system/hermes-triage.service.d/override.conf")
p.parent.mkdir(parents=True, exist_ok=True)
text = p.read_text() if p.exists() else "[Service]\n"
if "PC_HOST=" not in text:
    if not text.endswith("\n"):
        text += "\n"
    text += "Environment=PC_HOST=100.124.41.115\n"
    p.write_text(text)
    print("PC_HOST injected")
else:
    print("PC_HOST already present")
print(p.read_text())
PY

# timezone
timedatectl set-timezone America/Chicago || true

# ssh host keys last
cp -a "$SRC/etc/ssh/ssh_host_"* /etc/ssh/
chmod 600 /etc/ssh/ssh_host_*_key
chmod 644 /etc/ssh/ssh_host_*.pub
systemctl reload ssh || systemctl reload sshd || true

systemctl daemon-reload
echo APPLY_DONE
id mavshare hermes node_exporter syncthing
ls /etc/systemd/system/hermes-gateway.service /etc/systemd/system/orca-aiwa.service
grep -n '\[proxmox-root\]' /etc/samba/smb.conf && echo BAD_SHARE || echo SHARE_OK
date
hostname
ip -4 -br addr
pct list
curl -sS -m 4 http://192.168.1.240:8080/health; echo
