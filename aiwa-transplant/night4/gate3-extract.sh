#!/bin/bash
# Gate 3.2 — selective extract. Never untar /etc wholesale. Never restore /etc/pve or /etc/network.
set -x
exec > /root/gate3-extract.log 2>&1
TAR=/var/lib/vz/dump/aiwa-host-state-20260822.tar.gz
DEST=/mnt/samsung-stage/host-restore
mkdir -p "$DEST"
tar -C "$DEST" -xzf "$TAR" \
  root \
  opt \
  home \
  usr/local/bin \
  var/lib/docker/volumes \
  var/lib/samba \
  var/spool/cron \
  var/lib/tailscale \
  etc/systemd/system/hermes-gateway.service \
  etc/systemd/system/hermes-triage.service \
  etc/systemd/system/hermes-triage.service.d \
  etc/systemd/system/hermes-customer-sms.service \
  etc/systemd/system/hermes-pc-sms.service \
  etc/systemd/system/pacc-registry.service \
  etc/systemd/system/orca-aiwa.service \
  etc/systemd/system/node_exporter.service \
  etc/systemd/system/node_exporter.service.d \
  etc/systemd/system/hcp-catalog-sync.service \
  etc/systemd/system/hcp-catalog-sync.timer \
  etc/systemd/system/hcp-estimates-sync.service \
  etc/systemd/system/hcp-estimates-sync.timer \
  etc/systemd/system/mav-pve-storage-metrics.service \
  etc/systemd/system/mav-pve-storage-metrics.timer \
  etc/systemd/system/pm2-root.service \
  etc/samba/smb.conf \
  etc/ssh/ssh_host_rsa_key \
  etc/ssh/ssh_host_rsa_key.pub \
  etc/ssh/ssh_host_ecdsa_key \
  etc/ssh/ssh_host_ecdsa_key.pub \
  etc/ssh/ssh_host_ed25519_key \
  etc/ssh/ssh_host_ed25519_key.pub \
  etc/passwd \
  etc/shadow \
  etc/group \
  etc/gshadow \
  etc/timezone

echo EXTRACT_DONE
du -sh "$DEST"/* "$DEST"/var/lib/* 2>/dev/null
ls "$DEST"/etc/systemd/system | grep -i llama || echo NO_LLAMA_UNIT_EXTRACTED
