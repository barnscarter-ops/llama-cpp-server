#!/bin/bash
set -x
exec > /root/gate3-services.log 2>&1
systemctl daemon-reload
systemctl enable --now \
  hermes-gateway \
  hermes-triage \
  hermes-customer-sms \
  hermes-pc-sms \
  pacc-registry \
  orca-aiwa \
  node_exporter \
  syncthing@syncthing \
  pm2-root
systemctl enable --now \
  hcp-catalog-sync.timer \
  hcp-estimates-sync.timer \
  mav-pve-storage-metrics.timer
export PM2_HOME=/root/.pm2
pm2 resurrect || true
echo "===UNIT==="
for u in hermes-gateway hermes-triage hermes-customer-sms hermes-pc-sms pacc-registry orca-aiwa node_exporter syncthing@syncthing pm2-root smbd nmbd tailscaled; do
  printf '%-28s %s\n' "$u" "$(systemctl is-active "$u")"
done
echo "===FAILED==="
systemctl --failed --no-pager
echo "===PM2==="
pm2 list
echo "===LISTEN==="
ss -lntp | grep -E ':6768|:8001|:9100|:8384|:445|:8080' || true
echo "===SUBNET==="
tailscale debug prefs 2>/dev/null | grep -i route || tailscale status
echo SERVICES_DONE
