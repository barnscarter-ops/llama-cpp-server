# Night 4 — next (NOT started)

**Date:** 2026-08-19  
**Gate:** Night 3 PASS (`NIGHT3-OBSERVED-20260819.md`). Soak days before cutover.  
**PoC:** `aiwa-poc` @ **192.168.1.230** (not `.13` — that is production Orca). SSH: `id_ed25519_proxmox`. UI: `:8006`. CT 200 rustdesk **stopped**.

## Do not

- Steal `192.168.1.12` / hostname `aiwa` until Carter says go  
- Restore CT 102/103 onto the PoC  
- Touch production AIWA without `aiwa-deployment.md` approval  
- Use the 08-17 dumps as the final cutover set  

## When Carter opens Night 4

1. Write a full Night 4 plan (this file is not that plan).  
2. Ensure bind-mounts exist on the new host **before** first CT start (`mp0` `/mnt/samsung-sata/mav-transfer` on CT 100).  
3. Fresh incremental vzdump on ProDesk; shut CTs; restore all four; move SN770; identity `.12`; Tailscale; Realtek NIC to Z690.  
4. ProDesk stays intact as rollback until soak.

Boot lesson (Z690): Secure Boot OS Type **Other OS** + Ventoy GPT + USB-A adapter. Rufus DD was rejected on this board.
