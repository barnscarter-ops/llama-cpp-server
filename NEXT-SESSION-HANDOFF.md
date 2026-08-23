# NEXT-SESSION-HANDOFF — Night 4 complete (2026-08-22)

**Repo:** `D:\Workspace\Infrastructure\llama-cpp-server`
**Evidence:** `aiwa-transplant/NIGHT4-COMPLETE-20260822.md`
**Doctrine:** `C:\Workspace\Active\brain\knowledge\local-llm-architecture.md`

Carter confirmed Night 4 is done. Production AIWA = Z690 Proxmox `aiwa` at
`192.168.1.12`. ProDesk off. Clerk `http://192.168.1.240:8080` Nemotron.

## Closed

Gates 0–4 of `aiwa-transplant/NIGHT4-PLAN.md`. Identity swap happened. Do not
treat `.230` / `aiwa-poc` as the live host. Do not start Gate 1.

## Soak leftovers (not a second cutover)

- Voice docker failed Gate 4
- Syncthing peers not up (IDs ok)
- Samba `mavshare` auth from Windows against the new host
- Pair Orca to live `aiwa`

## Do not

- Power on the ProDesk while `.12` is on the Z690
- Dual llama-server; destroy CT 210; start host `night4/llama-server.service`
- Move the SN770
- Start Mav Room Session J until soak **and** Carter says go
