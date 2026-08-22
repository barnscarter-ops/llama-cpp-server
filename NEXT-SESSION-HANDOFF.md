# NEXT-SESSION-HANDOFF — Night 4 cutover (Gate 1 start)

**Repo:** `D:\Workspace\Infrastructure\llama-cpp-server` `main` origin **`e8c82de`**
**Runbook:** `aiwa-transplant/NIGHT4-PLAN.md` (2026-08-22 overlay at top **wins**)
**Night-of card:** `aiwa-transplant/NIGHT4-NEXT.md`
**Doctrine:** `C:\Workspace\Active\brain\knowledge\local-llm-architecture.md`

Carter is ready to begin the cutover. **This session did not start Gate 1.** Next agent starts Gate 1 after Carter says go (he already said he is ready — treat that as go unless he paused).

## Closed this session (verified)

- Gate 0 **done ~11:30 CDT**. Protected snapshot vzdumps 100–103 + host tarball. SMART PASSED SN770 + 840 PRO.
- Copies **checksum-OK in three places:** ProDesk `/var/lib/vz/dump/`, `C:\aiwa-backups\20260822\`, PoC `/var/lib/vz/dump/`.
- Docker on ProDesk: down ~5 min for tar, **7/7 back**. CTs 100–103 still running at handoff. `.12:8006` still open.
- `mavshare` proven on live `\\192.168.1.12\Proxmox`. Mapping not left connected.
- **NIC correction:** Realtek `1C-86-0B-3A-48-FB` is PC `AIWA Direct` `10.110.10.2`. **Stays in the X870E.** Night 4 “move it to the 690” was a mix-up with ProDesk `p2p0` (`10.110.10.1`). Gate 1 is ProDesk-off **only**. No 870 shutdown, no card pull.
- Gate 4 is **keep CT 210**, not host Q4 `:8090`. Repair from `690-routing/` if `.240` dies.
- At handoff: `http://192.168.1.240:8080` = `nemotron-3.5-lightning-30b-a3b` Q5_K_M 131k, health ok. (Local LLM Board did a live swap earlier today; walk-away clerk restored.)

## Locked (Carter)

1. SN770 stays in ProDesk.
2. Voice / customer-SMS on hold — stop anytime.
3. CT 200 stays **stopped** (do not destroy). **Never destroy 210.**
4. `mavshare` password known (Explorer / `net use` succeeded).
5. Card `1C-86-…` stays in the 870.

## Gate 0 artifacts (sha256)

```
587c2a709f5b318941785a738be120d924d872a4b7e2f0802d45eb122a66560a  vzdump-lxc-100-2026_08_22-11_14_32.tar.zst
856c840809144d76954a444ec01968f4781ecb1266ef0b0f57dc457408ca414c  vzdump-lxc-101-2026_08_22-11_14_44.tar.zst
f66aea6e8ba81a160645daf62db7f268ac5c8685caa0c85b82b83d93dd41404d  vzdump-lxc-102-2026_08_22-11_15_07.tar.zst
70aa1d0b7ef194d8bc4a9de5506fea331b801c4eefe767d1dd4e003d76a9b697  vzdump-lxc-103-2026_08_22-11_15_37.tar.zst
91264ddc40b31035c7e7fe4177f51a07ed38cb567f8084f80434704cf3fd5f8b  aiwa-host-state-20260822.tar.gz
```

## Start here (Gate 1)

Announce on `C:\Workspace\Active\brain\WORKBOARD.md` first.

On **ProDesk** (`ssh -i ~/.ssh/id_ed25519_proxmox root@10.110.10.1` or `.12`):

```
pct stop 100 101 102 103      # verify all four down
qm list                       # expect empty
tailscale down
shutdown -h now
```

From X870E: confirm `192.168.1.12` and `10.110.10.1` stop answering. Then Gate 2 on **aiwa-poc** `192.168.1.230` (dumps already there).

## Gate 2–4 traps (do not re-derive)

- Bind-mount `/mnt/samsung-sata` **before** first CT start (`cp -a` from `/mnt/samsung-stage/`).
- `pct destroy 200` only if Carter revisits — currently **keep stopped**. Never 210.
- Gate 3: install **only** `10-lan0.link` (I225-V `58:11:22:30:68:48`). **Do not** install `11-p2p0.link` (pins the 870’s MAC). vmbr0 → `.12`. No vmbr1 tonight. Drop `[proxmox-root]`. Re-point triage `PC_HOST` → `100.124.41.115`. `smbpasswd` only if samba restore fails.
- Gate 4: `curl http://192.168.1.240:8080/health` + `/v1/models`. Do not start `aiwa-transplant/night4/llama-server.service`.
- Rollback: Z690 off, ProDesk on. Do not ping-pong.
- `pkill -f llama-server` self-kills — use `[l]lama-server` after `systemctl stop`.
- Do not steal `.12` while ProDesk is up. Do not dual llama-server.

## Do not redo

Gate 0 dumps, mavshare test, “move the 870 Realtek,” host Q4 llama bring-up, SN770 move, CT 210 destroy.
