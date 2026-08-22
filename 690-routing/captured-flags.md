# Captured live flags — CT 210 llama-vulkan

**Captured:** 2026-08-22 00:16–00:25 CT (Session 1)
**Host:** Proxmox `aiwa-poc` `192.168.1.230` → `lxc-attach -n 210`
**Endpoint:** `http://192.168.1.240:8080`
**Night 4:** plan is tonight ~18:30; at capture time (~00:16 CT) cutover had **not** started. Live SSH was allowed. No llama restart this session.

Nothing below is invented. Nemotron argv is live `ps` + the systemd unit. Qwen argv is leftover scripts on `/opt/llama/` plus the last `:8080` consult log.

---

## Probe

```
GET http://192.168.1.240:8080/v1/models   (curl -sS --max-time 5)
GET http://192.168.1.240:8080/health
GET http://192.168.1.240:8080/props
```

| Check | Result |
|---|---|
| `/v1/models` | `id` / `name` = `nemotron-3.5-lightning-30b-a3b` (aliases same). `n_ctx` 131072, `n_ctx_train` 1048576, `ftype` Q5_K Medium, `size` 26956282112 |
| `/health` | `{"status":"ok"}` |
| `/props` | `model_alias` `nemotron-3.5-lightning-30b-a3b`, `n_ctx` 131072, `total_slots` 4, `reasoning_format` `none`, `model_path` Nemotron Q5_K_M, vision false |
| `systemctl is-active llama-server` | `active` |
| `systemctl is-enabled llama-server` | `enabled` |
| MainPID | 895 (`NRestarts=0`) |
| Manual Qwen process | **none** — only systemd's llama-server |

Carter was not mid-consult. Clerk default is already restored. **No WORKBOARD llama-restart announcement and no `systemctl`/pkill this session.**

---

## GGUF paths (`/opt/llama/models/`)

| File | Bytes | Role |
|---|---:|---|
| `NVIDIA-Nemotron-3.5-Lightning-30B-A3B-Q5_K_M.gguf` | 26964179424 | clerk weights (systemd) |
| `Qwen3.8-27B-UD-Q6_K.gguf` | 21983677344 | consult weights |
| `mtp-Qwen3.8-27B-Q4_0.gguf` | 1369590656 | Qwen MTP draft head (`-md`) |

Binary: `/opt/llama/src/build/bin/llama-server` — `version: 0.1.2-dev (build 1, commit a302733)`. No mmproj on this models dir.

---

## Current `--alias` values

| Seat | Alias | Where |
|---|---|---|
| Nemotron (live + unit) | `nemotron-3.5-lightning-30b-a3b` | `--alias` on systemd ExecStart. Matches pi `llamacpp-690`. **No** second `--alias local-llm`. |
| Qwen A/B leftover | `qwen3.8-27b` | `/opt/llama/qwen-ab.sh` only (131k, port **8081**) |
| Qwen 262k leftover | *(none passed)* | `/opt/llama/qwen-ctx.sh` has no `--alias`. Last `:8080` consult log matches this script's ctx/MTP/port-8080 shape. |

Session 2 should add `--alias qwen3.8-27b` on the consult swap. Do not invent a `local-llm` alias on Nemotron — the live unit does not have it.

---

## Nemotron clerk — systemd unit (verbatim)

Path: `/etc/systemd/system/llama-server.service` (mtime 2026-08-21 14:00 UTC)

```
[Unit]
Description=llama.cpp server (Vulkan) - Nemotron 3.5 Lightning 30B-A3B Q5_K_M
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/opt/llama/src/build/bin/llama-server   -m /opt/llama/models/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-Q5_K_M.gguf   --host 0.0.0.0 --port 8080   -ngl 999 -c 131072 -b 2048 -ub 512   --jinja --reasoning off   --spec-type draft-mtp   --alias nemotron-3.5-lightning-30b-a3b
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Live argv (pid 895, `ps -ww -C llama-server -o pid,user,args=`), matches the unit:

```
/opt/llama/src/build/bin/llama-server -m /opt/llama/models/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-Q5_K_M.gguf --host 0.0.0.0 --port 8080 -ngl 999 -c 131072 -b 2048 -ub 512 --jinja --reasoning off --spec-type draft-mtp --alias nemotron-3.5-lightning-30b-a3b
```

Notes vs leftover `nem-ab.sh` (bench-only, **not** production): that script used `-c 65536`, `--port 8081`, `-fa on`, `--alias nemotron-test`. Live clerk does **not** pass `-fa` or `-ctk`/`-ctv`.

---

## Qwen consult — leftover scripts (verbatim command construction)

### 262k ctx probe — `/opt/llama/qwen-ctx.sh` (2026-08-20 20:25 UTC)

This is the proven 262k + MTP + q8_0 KV line. `$CTX` was `262144` (`qwen-ctx-262144.log`). Port **8081**. **No** `--alias`. **No** `--reasoning off`.

```
/opt/llama/src/build/bin/llama-server \
  -m /opt/llama/models/Qwen3.8-27B-UD-Q6_K.gguf \
  -md /opt/llama/models/mtp-Qwen3.8-27B-Q4_0.gguf \
  --spec-type draft-mtp \
  --host 0.0.0.0 --port 8081 \
  -ngl 999 -c 262144 \
  -fa on -ctk q8_0 -ctv q8_0 \
  --jinja
```

Log: `n_slots = 4, n_ctx_slot = 262144, kv_unified = 'true'`; listening `http://0.0.0.0:8081`; chat template "supports preserving reasoning" (thinking left ON).

### A/B 131k — `/opt/llama/qwen-ab.sh` (2026-08-20 20:09 UTC)

Port **8081**. This is the only leftover that passes `--alias qwen3.8-27b`. MTP on when `$MODE=on`:

```
/opt/llama/src/build/bin/llama-server \
  -m /opt/llama/models/Qwen3.8-27B-UD-Q6_K.gguf \
  --host 0.0.0.0 --port 8081 \
  -ngl 999 -c 131072 \
  -fa on -ctk q8_0 -ctv q8_0 \
  --jinja --alias qwen3.8-27b \
  -md /opt/llama/models/mtp-Qwen3.8-27B-Q4_0.gguf \
  --spec-type draft-mtp
```

### Last `:8080` consult — `/opt/llama/qwen262.log` (mtime 2026-08-21 08:05 UTC)

No argv in CT bash history (empty/missing). Log matches **qwen-ctx.sh** at 262k, but listening on **`http://0.0.0.0:8080`**:

- `loading model '/opt/llama/models/Qwen3.8-27B-UD-Q6_K.gguf'`
- `n_gpu_layers already set by user to 999`
- draft `mtp-Qwen3.8-27B-Q4_0.gguf`
- `n_slots = 4, n_ctx_slot = 262144, kv_unified = 'true'`
- thinking ON (`--reasoning-preserve` suggested; `--reasoning off` was **not** passed)

Earlier same morning: `/opt/llama/qwen-serve.log` (06:54 UTC) is the same weights/MTP on `:8080` but `n_ctx_slot = 131072` (131k consult, not the 262k seat).

**Consult argv to copy into Session 2 `swap-qwen-consult.sh`** (last 262k `:8080` consult = qwen-ctx flags with port 8080). Cache-type flags do not print in these logs; they come from `qwen-ctx.sh` / `qwen-ab.sh`, not from grepping the server log:

```
/opt/llama/src/build/bin/llama-server \
  -m /opt/llama/models/Qwen3.8-27B-UD-Q6_K.gguf \
  -md /opt/llama/models/mtp-Qwen3.8-27B-Q4_0.gguf \
  --spec-type draft-mtp \
  --host 0.0.0.0 --port 8080 \
  -ngl 999 -c 262144 \
  -fa on -ctk q8_0 -ctv q8_0 \
  --jinja
```

Add `--alias qwen3.8-27b` in Session 2 (present on the 131k A/B script, absent on the 262k script). Do not add `--reasoning off`.

---

## `/opt/llama/` operator files (not src tree)

`nem-ab.sh`, `qwen-ab.sh`, `qwen-ctx.sh`, plus bench/serve logs (`qwen-serve.log`, `qwen262.log`, `qwen-ctx-262144.log`, `*-server-on.log`, `*-server-off.log`). No swap scripts yet.

---

## Do not confuse with Night 4 draft unit

`aiwa-transplant/night4/llama-server.service` is a **stale** Gate-4 draft (Q4_K_M, `-c 65536`, `--port 8090`, `--alias local-llm`, `--reasoning off`, no MTP). It is **not** what CT 210 is running.
