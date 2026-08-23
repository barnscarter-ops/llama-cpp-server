# Night 4 — COMPLETE 2026-08-22

Carter confirmed the cutover night is done. Production **AIWA is the Z690**
(Proxmox hostname `aiwa`) at **`192.168.1.12`**. The HP ProDesk is off
(rollback box; SN770 stayed in it). PoC identity `aiwa-poc` / `.230` is retired.

Runbook: `NIGHT4-PLAN.md`. Gate 0 dumps: `C:\aiwa-backups\20260822\`.

## What is live

- Host: Z690 + 13600K + R9700, LAN `.12`, UI `:8006`
- CTs restored from the 08-22 set; CT 210 llama-vulkan kept
- Clerk: `http://192.168.1.240:8080` Nemotron (Gate 4 clerk/Hermes/CTs/console pass)
- Realtek `1C-86-0B-3A-48-FB` stayed in the X870E (`AIWA Direct`)
- SN770 did **not** move

## Follow-ups (soak, not re-cutover)

- Voice docker failed Gate 4
- Syncthing IDs ok; peers not up yet
- Samba `mavshare` needs auth from Windows against the new host
- Pair Orca to the live identity
- Session J (Mav Room fabric → AIWA Tailscale) still after soak **and** Carter go

Do not power the ProDesk on while `.12` is live. Rollback is: Z690 off, then ProDesk on.
