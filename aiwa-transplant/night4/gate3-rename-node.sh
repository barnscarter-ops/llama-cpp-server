#!/bin/bash
# Move standalone PVE node dir aiwa-poc -> aiwa. Guests stay running.
set +e
exec > /root/gate3-rename-node.log 2>&1
set -x

systemctl stop pvedaemon pveproxy pvestatd pvescheduler pve-ha-lrm pve-ha-crm pve-cluster
sleep 2
for i in 1 2 3 4 5; do
  pgrep pmxcfs || break
  killall pmxcfs
  sleep 1
done
pgrep pmxcfs && killall -9 pmxcfs
sleep 1

pmxcfs -l
sleep 1

echo "=== nodes before ==="
ls -la /etc/pve/nodes
ls -la /etc/pve/nodes/aiwa 2>/dev/null
ls -la /etc/pve/nodes/aiwa-poc 2>/dev/null

if [ -d /etc/pve/nodes/aiwa ]; then
  find /etc/pve/nodes/aiwa -mindepth 1 -depth -exec rm -rf {} +
  rmdir /etc/pve/nodes/aiwa
  rm -rf /etc/pve/nodes/aiwa
fi

echo "=== after stub remove ==="
ls -la /etc/pve/nodes

mv /etc/pve/nodes/aiwa-poc /etc/pve/nodes/aiwa
echo "mv_rc=$?"

echo "=== nodes after ==="
ls -la /etc/pve/nodes
ls -la /etc/pve/nodes/aiwa/lxc

killall pmxcfs
sleep 1
pgrep pmxcfs && killall -9 pmxcfs
sleep 1

systemctl start pve-cluster
sleep 2
systemctl start pvedaemon pveproxy pvestatd pvescheduler

if [ -d /var/lib/rrdcached/db/pve2-node/aiwa-poc ]; then
  mkdir -p /var/lib/rrdcached/db/pve2-node/aiwa
  mv /var/lib/rrdcached/db/pve2-node/aiwa-poc/* /var/lib/rrdcached/db/pve2-node/aiwa/ 2>/dev/null
  rmdir /var/lib/rrdcached/db/pve2-node/aiwa-poc 2>/dev/null
fi

sleep 2
echo "=== pct list ==="
pct list
echo "=== pct status 210 ==="
pct status 210
echo RENAME_DONE
