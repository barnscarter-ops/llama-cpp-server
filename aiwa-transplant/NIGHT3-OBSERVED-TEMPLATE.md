# Night 3 observed — FILL IN

**Date:** 2026-08-__  
**Operator:** Carter  
**Host:** aiwa-poc @ 192.168.1.13  
**ISO:** proxmox-ve_9.2-1  

## Hardware / BIOS

- [ ] Toshiba removed  
- [ ] VMD disabled  
- [ ] Booted via `UEFI: <USB>`  
- [ ] Installer disk = 1 TB SN7100 only  

Notes:

```
```

## Install

- Hostname: `aiwa-poc`
- IP: `192.168.1.13/24`
- Gateway: `192.168.1.254`
- Web UI `https://192.168.1.13:8006` reachable from X870E? Y/N  

`pveversion -v` (paste):

```
```

## Task 5 — rustdesk CT100 → CT 200 on vmbr99

SHA256 on PoC (expect `be61487d…b8fc5`):

```
```

| Step | Result |
|------|--------|
| `pct restore 200 …` | |
| `pct set 200 --net0 … vmbr99` | |
| `pct start 200` | |
| `pct exec 200 -- ps aux` | |
| `pct exec 200 -- systemctl --failed` | |
| `pct stop 200` | |

**VERDICT:** PASS / FAIL  

If FAIL, paste error + whether Night 4 is blocked (it is, until fixed):

```
```

## Deliberately not done

- [x] No Tailscale on PoC  
- [x] No identity `.12`  
- [x] No CT 102/103 restore  
- [x] No AIWA production touch  
