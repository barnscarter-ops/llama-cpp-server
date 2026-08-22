# Night 4 — GO-LIVE CARD (Saturday 2026-08-22)

**Runbook:** [`NIGHT4-PLAN.md`](NIGHT4-PLAN.md) — read the **2026-08-22 overlay** at the top, then the gates. Overlay wins over 08-19 Gate 4.

Prep verified **10:29 CDT**. Scheduled start **~18:30**. Do not steal `192.168.1.12`.

## Right now — Gate 0 DONE (2026-08-22 ~11:30 CDT)

- Cutover set **taken, copied, checksum-OK** in three places: ProDesk `/var/lib/vz/dump/`, `C:\aiwa-backups\20260822\`, PoC `/var/lib/vz/dump/`.
- ProDesk still production: CTs 100–103 running, docker 7/7 up (restarted ~5 min for host tar).
- PoC: CT 200 stopped, CT 210 running. Llama `.240` not touched this gate.
- Fallback remains `C:\aiwa-backups\20260817\`.
- Next: **go Gate 1** (Carter physical: ProDesk down, pull Realtek `1C-86-0B-3A-48-FB` PCI bus 16).

## Locked (Carter, 2026-08-22)

1. SN770 stays in the ProDesk.
2. Voice / customer-SMS on hold — stop anytime.
3. CT 200 stays **stopped** (not destroyed).
4. `mavshare`: **proven** (`net use \\192.168.1.12\Proxmox /user:mavshare` succeeded). Mapping not left connected.
5. Gate 0 **done**. Waiting: **go Gate 1**.

## Test `mavshare` now (do this in your own terminal)

Share is live: `[Proxmox]` → `/mnt/samsung-sata/mav-transfer`, user `mavshare`.
TCP 445 is open on **both** `192.168.1.12` and `10.110.10.1`. Windows has **no**
saved mavshare credential. Do **not** open `[proxmox-root]` — that share is
guest/root and proves nothing.

In **your** PowerShell or cmd (the `*` prompts; password never hits agent logs):

```
net use P: \\192.168.1.12\Proxmox /user:mavshare *
dir P:\
net use P: /delete
```

Direct-link fallback if LAN auth acts weird: `\\10.110.10.1\Proxmox`.

Pass = folder listing. Fail = `53` (name) or `86`/`1326` (wrong password).
`C:\aiwa-840pro\mav-transfer` is a file copy and does **not** test Samba.

After Gate 3, the same password should work if `/var/lib/samba/private` restored
from the host tarball. `smbpasswd -a mavshare` is only the backup if that fails.

## Order (⛔ each gate)

| Gate | Window | Agent vs Carter |
|---|---|---|
| 0 final dumps on ProDesk | 18:30–19:50 | Orca on **production CT 101 / host**. scp off via **10.110.10.1** before the card moves. docker stop only ~5 min for host tarball. |
| 1 ProDesk down + card move | 19:50–20:15 | Carter physical: confirm `.12` and `10.110.10.1` dead, pull Realtek, seat in Z690, X870E back on `HomeFiber` only. |
| 2 restore CTs 100–103 on `.230` | 20:15–20:50 | bind-mount `/mnt/samsung-sata` **before** first start. Destroy **200** only if Carter said so. **Never 210.** |
| 3 identity → `.12` + host config | 20:50–22:20 | `.link` files already at `/root/night4-staging/`. No wholesale `/etc`. Drop `[proxmox-root]`. Re-point triage `PC_HOST` → `100.124.41.115`. |
| 4 llama + e2e | 22:20–23:00 | `curl .240 /health` + `/v1/models`. If dead, re-apply `690-routing/`. Then the map's service checks. |

## Gate 0 artifacts (sha256)

```
587c2a709f5b318941785a738be120d924d872a4b7e2f0802d45eb122a66560a  vzdump-lxc-100-2026_08_22-11_14_32.tar.zst
856c840809144d76954a444ec01968f4781ecb1266ef0b0f57dc457408ca414c  vzdump-lxc-101-2026_08_22-11_14_44.tar.zst
f66aea6e8ba81a160645daf62db7f268ac5c8685caa0c85b82b83d93dd41404d  vzdump-lxc-102-2026_08_22-11_15_07.tar.zst
70aa1d0b7ef194d8bc4a9de5506fea331b801c4eefe767d1dd4e003d76a9b697  vzdump-lxc-103-2026_08_22-11_15_37.tar.zst
91264ddc40b31035c7e7fe4177f51a07ed38cb567f8084f80434704cf3fd5f8b  aiwa-host-state-20260822.tar.gz
```

Rollback: Z690 off, ProDesk on. After identity swap, same, and do not ping-pong.

## Do not

- Start host Q4 llama / dual llama-server
- Destroy CT 210
- Claim `.12` while ProDesk is up
- Restore 102/103 onto the PoC before Gate 1
- Use the 08-17 dumps as the cutover set
- Restart Mav-Room fabric / Hermes / pc-actions-daemon from that repo
- `pkill -f llama-server` (self-kill) — use `[l]lama-server` after `systemctl stop`
