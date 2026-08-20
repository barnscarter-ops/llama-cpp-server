# Night 4 — SCHEDULED: Saturday 2026-08-22 evening

**The plan now exists: [`NIGHT4-PLAN.md`](NIGHT4-PLAN.md) — that is the runbook.**

- Locked 2026-08-19 by Carter: **Sat 08-22 evening**, start ~18:30, downtime
  ~19:50–23:00 (3 h), 3 soak days on the PoC before it.
- 840 PRO: only service dirs restored (mav-transfer, mav-rag/hcp-exports);
  the rest is media already on Google Drive.
- Llama model: already picked/benched/tuned — Gate 4 is bring-up + a 10-min
  KV sanity check on Vulkan, not a model debate.
- Prep session: Thu 08-20 or Fri 08-21 evening (~1–2 h) — Night 4 plan §Pre-flight.
- Open item for the night: SN770 — NIGHT4-NEXT said "move", transplant-map says
  "never moves". Plan recommends NOT moving it on cutover night (ProDesk must
  stay intact as rollback); Carter confirms on the night.

Gate: Night 3 PASS (`NIGHT3-OBSERVED-20260819.md`). PoC: `aiwa-poc` @
192.168.1.230, SSH `id_ed25519_proxmox`, UI :8006. CT 200 rustdesk stopped.

## Do not (until the night)

- Steal `192.168.1.12` / hostname `aiwa` — identity swap is Gate 3
- Restore CT 102/103 onto the PoC
- Touch production AIWA without `aiwa-deployment.md` approval
- Use the 08-17 dumps as the cutover set (Gate 0 makes fresh ones)

Boot lesson (Z690): Secure Boot OS Type **Other OS** + Ventoy GPT + USB-A adapter.
