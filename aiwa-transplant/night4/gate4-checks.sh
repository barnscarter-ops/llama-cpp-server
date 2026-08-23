#!/bin/bash
# Gate 4 probes. Do not restart hermes, llama, or pm2 production apps.
exec > /root/gate4-checks.log 2>&1
set +e
pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; }
info() { echo "INFO  $*"; }

echo "===== GATE4 $(date) ====="

echo "----- clerk -----"
h=$(curl -sS -m 5 http://192.168.1.240:8080/health)
echo "health=$h"
echo "$h" | grep -q '"status":"ok"' && pass "clerk health" || fail "clerk health $h"
mods=$(curl -sS -m 5 http://192.168.1.240:8080/v1/models)
echo "$mods" | grep -q 'nemotron-3.5-lightning-30b-a3b' && pass "clerk alias nemotron-3.5-lightning-30b-a3b" || fail "clerk alias"
echo "$mods" | grep -q 'n_ctx.:131072\|"n_ctx": 131072' && pass "clerk n_ctx 131072" || info "n_ctx check via models json"
ss -lntp | grep -q ':8090' && fail "host :8090 llama present (should be CT only)" || pass "no host :8090 llama"
pct status 210 | grep -q running && pass "pct 210 running" || fail "pct 210"
lxc-attach -n 210 -- systemctl is-active llama-server | grep -q active && pass "CT 210 llama-server active" || fail "CT 210 llama-server"

echo "----- cts -----"
pct list
for id in 100 101 102 103 210; do
  pct status "$id" | grep -q running && pass "CT $id running" || fail "CT $id not running"
done
pct status 200 | grep -q stopped && pass "CT 200 stopped" || fail "CT 200 not stopped"

echo "----- rustdesk 100 -----"
pct exec 100 -- systemctl is-active hbbs hbbr
pct exec 100 -- systemctl is-active hbbs | grep -q active && pass "hbbs" || fail "hbbs"
pct exec 100 -- systemctl is-active hbbr | grep -q active && pass "hbbr" || fail "hbbr"
pct exec 100 -- findmnt /mnt/mav-transfer >/dev/null && pass "rustdesk mp0 mav-transfer" || fail "rustdesk mp0"
ss -lntp | grep -E ':2111[5-9]|:21116' || info "rustdesk ports on host (may be inside CT)"
pct exec 100 -- ss -lntp | grep -E '2111' || true

echo "----- orca 101 .13 -----"
ping -c 1 -W 1 192.168.1.13 >/dev/null && pass "ping .13" || fail "ping .13"
pct exec 101 -- hostname
pct exec 101 -- ss -lntp | head -40
curl -skS -m 5 -o /dev/null -w "orca_http=%{http_code}\n" http://192.168.1.13:6768/ || true
curl -skS -m 5 -o /dev/null -w "orca_443=%{http_code}\n" https://192.168.1.13/ || true
ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@192.168.1.13 true 2>/dev/null && pass "ssh .13" || info "ssh .13 not with this key (expected if orca user)"

echo "----- hcp-mcp 102 .14 -----"
ping -c 1 -W 1 192.168.1.14 >/dev/null && pass "ping .14" || fail "ping .14"
pct exec 102 -- hostname
pct exec 102 -- ss -lntp | head -40
pct exec 102 -- systemctl --failed --no-legend
# typical MCP ports
for p in 3000 8080 8000 3100 4000 8787 3333; do
  code=$(curl -sS -m 2 -o /dev/null -w "%{http_code}" http://192.168.1.14:$p/ 2>/dev/null)
  [ -n "$code" ] && [ "$code" != "000" ] && info "102 :$p -> $code"
done
touch /mnt/samsung-sata/mav-rag/hcp-exports/.gate4-write && rm -f /mnt/samsung-sata/mav-rag/hcp-exports/.gate4-write && pass "hcp-exports writable" || fail "hcp-exports writable"

echo "----- mcc-prod 103 console -----"
ping -c 1 -W 1 192.168.1.15 >/dev/null && pass "ping .15" || fail "ping .15"
code=$(curl -sS -m 5 -o /dev/null -w "%{http_code}" http://192.168.1.15:3000/)
[ "$code" = "200" ] && pass "mav-console LAN .15:3000 HTTP $code" || fail "mav-console LAN .15:3000 HTTP $code"
code=$(curl -sS -m 5 -o /dev/null -w "%{http_code}" http://127.0.0.1:3010/)
[ "$code" = "200" ] && pass "mav-console host :3010 HTTP $code" || info "host :3010 HTTP $code (docker leftover; console is CT 103)"
code=$(curl -sS -m 5 -o /dev/null -w "%{http_code}" http://100.87.155.47:3000/)
[ "$code" = "200" ] && pass "mav-console tailscale :3000 HTTP $code" || fail "mav-console tailscale :3000 HTTP $code"

echo "----- hermes -----"
for u in hermes-gateway hermes-triage hermes-customer-sms hermes-pc-sms; do
  systemctl is-active "$u" | grep -q active && pass "$u active" || fail "$u"
  systemctl is-enabled "$u" | grep -q enabled && pass "$u enabled" || fail "$u enabled"
done
ss -lntp | grep -E ':3013|:3014|:3015|:8642'
# triage PC_HOST
grep -n PC_HOST /etc/systemd/system/hermes-triage.service.d/override.conf || true
journalctl -u hermes-triage --since "10 min ago" --no-pager | tail -30
journalctl -u hermes-triage --since "10 min ago" --no-pager | grep -qi "can't reach\|cannot reach\|100.124.216.11" && fail "triage still targeting old PC" || pass "no recent triage can't-reach-old-PC"

echo "----- customer-chat / grizzly -----"
pm2 jlist | python3 -c 'import json,sys; apps=json.load(sys.stdin);
for a in apps:
 print(a["name"], a["pm2_env"].get("status"))'
ss -lntp | grep 3012 && pass "customer-chat :3012 listen" || fail "customer-chat :3012"

echo "----- samba -----"
systemctl is-active smbd | grep -q active && pass "smbd" || fail "smbd"
testparm -s 2>/dev/null | grep -A8 '\[Proxmox\]'
testparm -s 2>/dev/null | grep -q 'proxmox-root' && fail "insecure proxmox-root still present" || pass "no proxmox-root share"
pdbedit -L 2>/dev/null | grep -q mavshare && pass "mavshare in passdb" || fail "mavshare not in passdb"

echo "----- syncthing -----"
systemctl is-active syncthing@syncthing | grep -q active && pass "syncthing active" || fail "syncthing"
# API
STCFG=/home/syncthing/.local/state/syncthing/config.xml
[ -f "$STCFG" ] || STCFG=/home/syncthing/.config/syncthing/config.xml
[ -f "$STCFG" ] || STCFG=$(find /home/syncthing -name config.xml 2>/dev/null | head -1)
echo "stcfg=$STCFG"
if [ -n "$STCFG" ]; then
  python3 - << PY
from pathlib import Path
import xml.etree.ElementTree as ET, urllib.request
p=Path("$STCFG")
root=ET.parse(p).getroot()
devid=root.findtext("device/id") or root.find("device").get("id") if root.find("device") is not None else "?"
# my ID
my=root.find("device")
# actually GUI apikey
gui=root.find("gui")
key=gui.findtext("apikey") if gui is not None else None
print("device_id_first", root.find("device").get("id") if root.find("device") is not None else None)
devs=[d.get("id") for d in root.findall("device")]
print("devices", len(devs))
for d in devs[:8]:
    print("  ", d)
if key:
    req=urllib.request.Request("http://127.0.0.1:8384/rest/system/connections", headers={"X-API-Key": key})
    try:
        print(urllib.request.urlopen(req, timeout=5).read()[:1500].decode())
    except Exception as e:
        print("syncthing api error", e)
PY
fi

echo "----- docker leftover -----"
docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
docker logs voice-pipecat --tail 8 2>&1 | tail -8
docker logs mav-rag-api --tail 8 2>&1 | tail -8

echo "----- identity -----"
hostname
ip -4 -br addr
test -f /etc/systemd/network/10-lan0.link && grep MAC /etc/systemd/network/10-lan0.link
grep MAC /etc/systemd/network/11-p2p0.link
grep -q '1c:86:0b:3a:48:fb' /etc/systemd/network/11-p2p0.link && fail "staged 1C-86 p2p0.link installed" || pass "p2p0.link is ProDesk NIC MAC not 1C-86"

echo "----- tailscale -----"
tailscale status | head -12
tailscale debug prefs | grep -A5 AdvertiseRoutes

echo "GATE4_SCRIPT_DONE"
