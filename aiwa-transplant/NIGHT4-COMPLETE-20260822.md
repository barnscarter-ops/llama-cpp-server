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

- Samba `mavshare` from Workbench: `net use Z: \\192.168.1.12\Proxmox /user:mavshare /persistent:yes` (prompts for password; guest is blocked)
- Voice docker failed Gate 4 (optional; practice stack)
- Pair Orca to live `aiwa` if not already
- SN770 stays in the ProDesk until a later window
- Session J (Mav Room fabric → AIWA Tailscale) still after soak **and** Carter go

**Disregard:** rustdesk client reconnect (backup-of-backup, never used); Syncthing (learning leftover).

Hermes PC bridge (gateway, pc-sms, customer-sms model URL, triage) is Workbench Tailscale `100.124.41.115:8901` — not old CartersPC `100.124.216.11`. pc-sms Twilio `+14698741546` (inbound proven). Customer SMS `+14698963862` is a different allowlist.

Do not power the ProDesk on while `.12` is live. Rollback is: Z690 off, then ProDesk on.
