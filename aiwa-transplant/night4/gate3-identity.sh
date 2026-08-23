#!/bin/bash
# Gate 3.1 — identity. Do NOT install staged 11-p2p0.link (1C-86 is the X870E).
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

cp -a /etc/network/interfaces /etc/network/interfaces.pre-gate3
cp -a /etc/hosts /etc/hosts.pre-gate3

install -d /etc/systemd/network
cp /root/night4-staging/10-lan0.link /etc/systemd/network/10-lan0.link
cat > /etc/systemd/network/11-p2p0.link << 'EOF'
[Match]
MACAddress=a0:10:a3:a8:0e:e1

[Link]
Name=p2p0
EOF

cat > /etc/network/interfaces << 'EOF'
auto lo
iface lo inet loopback

auto lan0
iface lan0 inet manual

auto vmbr0
iface vmbr0 inet static
	address 192.168.1.12/24
	gateway 192.168.1.254
	bridge-ports lan0
	bridge-stp off
	bridge-fd 0

auto p2p0
iface p2p0 inet manual

auto vmbr1
iface vmbr1 inet static
	address 10.110.10.1/30
	bridge-ports p2p0
	bridge-stp off
	bridge-fd 0

auto vmbr99
iface vmbr99 inet manual
	bridge-ports none
	bridge-stp off
	bridge-fd 0

source /etc/network/interfaces.d/*
EOF

# Live rename so ifreload sees the names the .link files will persist.
if ip link show nic0 >/dev/null 2>&1; then
  ip link set nic0 name lan0
fi
if ip link show enp12s0 >/dev/null 2>&1; then
  ip addr flush dev enp12s0 || true
  ip link set enp12s0 name p2p0
  ip link set p2p0 up
fi

hostnamectl set-hostname aiwa
sed -i 's/192.168.1.230[[:space:]]aiwa-poc\.lan[[:space:]]aiwa-poc/192.168.1.12 aiwa.lan aiwa/' /etc/hosts
sed -i 's/aiwa-poc/aiwa/g' /etc/hosts

udevadm control --reload || true
ifreload -a
echo IDENTITY_DONE
hostname
ip -br link
ip -4 -br addr
