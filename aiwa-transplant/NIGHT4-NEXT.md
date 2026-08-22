# Night 4 — GO-LIVE CARD (Saturday 2026-08-22)

**Runbook:** [`NIGHT4-PLAN.md`](NIGHT4-PLAN.md) — read the **2026-08-22 overlay** at the top, then the gates. Overlay wins over 08-19 Gate 4.

Prep verified **10:29 CDT**. Scheduled start **~18:30**. Do not steal `192.168.1.12`.

## Right now (pre-Gate-0)

- ProDesk `.12` = production. PoC `.230` = soak host. Llama `.240` = **Nemotron clerk, healthy**.
- Cutover dumps **not taken**. Dest folder exists: `C:\aiwa-backups\20260822\`. Fallback: `C:\aiwa-backups\20260817\`.
- Card to pull from X870E: `AIWA Direct` MAC `1C-86-0B-3A-48-FB`, PCI bus 16. `HomeFiber` stays.
- SN770: **leave in ProDesk**.
- Llama: **keep CT 210**. Never install `night4/llama-server.service`. Repair from `690-routing/`.

## Locked (Carter, 2026-08-22)

1. SN770 stays in the ProDesk.
2. Voice / customer-SMS on hold — stop anytime.
3. CT 200 stays **stopped** (not destroyed).
4. `mavshare`: prove it on live ProDesk before Gate 0 (commands below).
5. Still waiting: **go Gate 0**.

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

Rollback: Z690 off, ProDesk on. After identity swap, same, and do not ping-pong.

## Do not

- Start host Q4 llama / dual llama-server
- Destroy CT 210
- Claim `.12` while ProDesk is up
- Restore 102/103 onto the PoC before Gate 1
- Use the 08-17 dumps as the cutover set
- Restart Mav-Room fabric / Hermes / pc-actions-daemon from that repo
- `pkill -f llama-server` (self-kill) — use `[l]lama-server` after `systemctl stop`
