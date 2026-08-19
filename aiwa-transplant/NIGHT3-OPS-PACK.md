# Night 3 ops pack — ready while you reflash / shower

**Date:** 2026-08-18  
**Do not touch production AIWA.** Night 3 is Z690 PoC only.

## Pre-flight (already done / verified on X870E)

| Item | Status |
|------|--------|
| Proxmox VE **9.2-1** ISO | `C:\Users\carte\Downloads\proxmox-ve_9.2-1.iso` |
| USB burned | You; reflashing with Rufus if boot fails |
| VMD disabled | You confirmed |
| CT100 dump hash | **PASS** — SHA256 matches `SHA256SUMS-20260817.txt` (`be61487d…b8fc5`) |
| Path | `C:\aiwa-backups\20260817\vzdump-lxc-100-2026_08_17-23_30_44.tar.zst` |

## Rufus reflash (USB-C stick)

1. Rufus → select the stick  
2. Select `proxmox-ve_9.2-1.iso`  
3. **Partition: GPT** · **Target: UEFI (non CSM)**  
4. Image mode: **DD Image** (not ISO mode) — ASUS Z690 is picky  
5. Flash, eject, then:

**Boot tip:** one-time boot menu (**F8/F10**), pick **`UEFI: <stick>`**. Prefer rear **USB-A 2.0** via your C→A adapter after reflash (same as before; DD mode is the variable that usually changes).

## Install targets (do not improvise)

| Setting | Value |
|---------|-------|
| Disk | **1 TB SN7100 only** (Toshiba physically out) |
| Hostname | **`aiwa-poc`** — never `aiwa` |
| IP | **`192.168.1.13/24`** |
| Gateway | `192.168.1.254` |
| NIC | onboard I225-V → `vmbr0` |

> Note: `NIGHT3-PLAN.md` Phase 4 once says UI at `.230` — treat that as a typo. Use **`.13`**.

Web UI after install: `https://192.168.1.13:8006`

## After install — Task 5 (from X870E, after PoC answers ping)

On **aiwa-poc** (SSH or console), create isolated bridge, then from **X870E**:

```powershell
# From X870E — push only the rustdesk dump + sum line (example; adjust key/user)
scp C:\aiwa-backups\20260817\vzdump-lxc-100-2026_08_17-23_30_44.tar.zst root@192.168.1.13:/root/
```

On **aiwa-poc**:

```bash
# isolated bridge (no LAN)
cat >> /etc/network/interfaces <<'EOF'

auto vmbr99
iface vmbr99 inet manual
	bridge-ports none
	bridge-stp off
	bridge-fd 0
EOF
ifreload -a

sha256sum /root/vzdump-lxc-100-2026_08_17-23_30_44.tar.zst
# expect: be61487df1cc08e9339ac43c31057a9506da239ab66e847a1d6faf0ebd4b8fc5

pct restore 200 /root/vzdump-lxc-100-2026_08_17-23_30_44.tar.zst --storage local-lvm
pct set 200 --net0 name=eth0,bridge=vmbr99
pct start 200
pct exec 200 -- ps aux
pct exec 200 -- systemctl --failed
pct stop 200
```

**Pass:** restore 0, CT starts, processes run, failures only if explained by isolation.  
**Then:** fill `NIGHT3-OBSERVED-2026-08-18.md` from the template.  
**Do not** restore CT 102/103 tonight.

## Aborts

- Still no `UEFI:` boot entry after DD reflash + USB2-A → try another stick if you have one  
- Installer sees no disk with VMD off → stop (BIOS/reseat)  
- Task 5 fails → **Night 4 blocked** (loud failure is success for Night 3)
