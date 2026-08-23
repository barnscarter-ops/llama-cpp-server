#!/bin/bash
# Rename pmxcfs node aiwa-poc -> aiwa by sqlite (stub dir already exists).
set -x
exec > /root/gate3-rename-node-db.log 2>&1

systemctl stop pvedaemon pveproxy pvestatd pvescheduler pve-ha-lrm pve-ha-crm pve-cluster
sleep 2
for i in 1 2 3 4 5 6 7 8; do
  pgrep pmxcfs || break
  killall pmxcfs
  sleep 1
done
pgrep pmxcfs && killall -9 pmxcfs
sleep 1

cp -a /var/lib/pve-cluster/config.db /root/config.db.pre-gate3-rename
cp -a /var/lib/pve-cluster/config.db-wal /root/config.db-wal.pre-gate3-rename 2>/dev/null || true

sqlite3 /var/lib/pve-cluster/config.db << 'SQL'
.headers on
.mode list
SELECT inode,parent,name FROM tree WHERE parent=212537 OR parent=212539 OR inode=212537;
BEGIN;
DELETE FROM tree WHERE parent=212539;
DELETE FROM tree WHERE parent=212545;
DELETE FROM tree WHERE parent=212537;
DELETE FROM tree WHERE inode=212537;
UPDATE tree SET name='aiwa', version=version+1 WHERE inode=12 AND name='aiwa-poc';
COMMIT;
SELECT inode,parent,name FROM tree WHERE name IN ('aiwa','aiwa-poc') OR parent=13;
SQL

echo SQLITE_DONE
systemctl start pve-cluster
sleep 3
systemctl start pvedaemon pveproxy pvestatd pvescheduler
sleep 2
echo "=== nodes ==="
ls -la /etc/pve/nodes
echo "=== lxc ==="
ls -la /etc/pve/lxc /etc/pve/nodes/aiwa/lxc
echo "=== pct list ==="
pct list
echo "=== status 210 ==="
pct status 210
echo DB_RENAME_DONE
