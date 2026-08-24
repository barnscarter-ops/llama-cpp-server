# Fleet default seat

When `FLEET_ROUTER=true` and a POST completion omits `model` or sends an empty
string, the guardian routes to **clerk** (AIWA, currently
`nemotron-3.5-lightning-30b-a3b`) instead of GLM. The guardian logs
`defaulted_model=clerk` at INFO on logger `guardian` whenever it applies this
default.

GLM clients must send an explicit model — `local-llm` or `qwen3-14b` — to
reach the workbench GLM seat while the fleet router is on.

The default is controlled by `FLEET_DEFAULT_SEAT` (accepted values: `clerk`,
`glm`; anything else falls back to `clerk`). `ecosystem.config.cjs` sets
`FLEET_DEFAULT_SEAT: "clerk"` on the llama-guardian env. The flag
`FLEET_ROUTER` itself is managed separately.

When `FLEET_ROUTER` is off, the env var is ignored and an empty model stays
GLM, exactly as before.
