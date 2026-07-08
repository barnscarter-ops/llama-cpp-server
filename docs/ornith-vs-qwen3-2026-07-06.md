# Ornith 1.0 9B Q8_0 vs Qwen3-14B Q4_K_L — Coding Quality Benchmark

**Date:** 2026-07-06
**Hardware:** RTX 4060 Ti 16GB, RTX 4060 Ti host
**Runner:** [`bench-coding.py`](../bench-coding.py) at temperature 0.2, seed 42, `max_tokens=4096`, one shot per prompt
**Raw results:** [`bench-ornith-9b.json`](../bench-ornith-9b.json), [`bench-qwen3-14b.json`](../bench-qwen3-14b.json)

Companion to the speed-only sweep already on file (Ornith wins prompt speed 6×, ties on generation). This one is about **output quality**.

## Verdict — Keep Qwen3-14B as daily driver

Qwen3-14B Q4_K_L beats Ornith 1.0 9B Q8_0 on every quality axis I could measure. The gap isn't marginal — it's 10/10 vs 7/10 on HumanEval, and Ornith burned its entire token budget in `<think>` loops on 2 of the 3 HumanEval failures **and** on 1 of the 3 workflow-coding prompts, producing no output at all. That's a production risk you can't paper over.

Where Ornith would still make sense: (a) a workload dominated by long prompts / large context inputs where its 6× prompt-processing lead matters more than generation, (b) a smaller-VRAM situation where you can't afford the Q4 14B, (c) a later Ornith release that fixes the reasoning-loop behavior.

## Headline scores

| Category | Ornith 9B Q8_0 | Qwen3-14B Q4_K_L | Notes |
|---|---|---|---|
| HumanEval (10 problems, pass@1) | 7/10 | **10/10** | Qwen3 clean sweep. Ornith failures below. |
| Tool calling (3 structured calls) | 3/3 | 3/3 | Both nailed all three: single call, choose-from-three, structured w/ enums+arrays. |
| Workflow rubric (5 prompts × /9) | 25/45 | **33/45** | See per-prompt breakdown. |
| Wall time, HumanEval (10 prompts) | ~5 min | ~5 min | Comparable — both dominated by thinking tokens. |
| Wall time, workflow (5 prompts) | ~7 min | ~7.6 min | Ornith slightly faster; both similar. |

## HumanEval failures (Ornith only)

Ornith's `<think>` reasoning is the failure mode, not its coding ability per se:

| ID | Problem | Failure mode |
|---|---|---|
| `he-03-truncate` | `truncate_number(float) -> float` (trivial) | **Reasoning loop.** Model spent 4096 tokens in `<think>` repeating "Wait, I'll check if I should use `int` to avoid the import" verbatim. `finish_reason=length`, `content=""`. |
| `he-06-intersperse` | Insert delimiter between consecutive elements | **Wrong algorithm.** Emitted `numbers[:1] + [delimiter]*(len-1) + numbers[1:]`. For `[1,2,3]` this yields `[1,4,4,2,3]` instead of `[1,4,2,4,3]`. Real coding bug. |
| `he-08-largest-prime-factor` | Largest prime factor of n | **Reasoning loop.** Same shape as `he-03`: "Wait, I can simplify slightly. Def largest_prime..." repeating. Full budget consumed in `<think>`, no output. |

Two of three failures are the same pathology: on some simple problems, Ornith's chain-of-thought gets stuck in a "wait, let me reconsider" loop and never emits final content. The one algorithmic mistake (`he-06`) is the kind of off-by-one a 9B model with less capacity is going to make sometimes.

## Workflow rubric (out of 9 per prompt: correctness /3, style /2, adherence /2, completeness /2)

| ID | Prompt | Ornith /9 | Qwen3 /9 | Comment |
|---|---|---|---|---|
| `wf-01` | Fix PowerShell path-with-spaces | 8 | 8 | Both wrap `$Path` in quotes. Ornith's edit is simpler (`"$Path"` inline); Qwen3 wraps the whole command in a quoted string for cmd.exe. Both work; different style. |
| `wf-02` | Refactor tangled Python function | **0** | 8 | Ornith: no output — `<think>` filled the whole 4096-token budget. Qwen3: clean, correct, preserves behavior, one loop, uses `isinstance(x, (int, float))`. |
| `wf-03` | Write pytest tests for `parse_duration` | 0 | 0 | Both hit length limit inside `<think>`, both emitted empty `content`. Tie on absence. |
| `wf-04` | Identify bug from stack trace | **9** | 8 | Both correctly identify `result = default` as the aliasing bug and propose `dict(default)`. Ornith is genuinely one-sentence per the prompt; Qwen3 adds a trailing "to ensure the original is not mutated" — minor prompt-adherence slip. |
| `wf-05` | Concurrency race condition | 8 | **9** | Both diagnose the non-atomic `+=` and fix with `threading.Lock`. Qwen3's explanation is slightly cleaner ("two threads read the same value, one increment is lost"), and it removes the now-stale `# sometimes fails` comment. Ornith leaves the comment in. |
| **Total** | | **25/45** | **33/45** | |

**Pattern:** Qwen3 wins on the two coding-heavy prompts; Ornith wins the concise-debug prompt; both tie on the pytest one that neither could produce within budget.

## Tool calling — full parity

Both models emit **native `tool_calls` JSON** (not inline markdown) via the `--jinja` chat template and pass all three structural checks:

| ID | Prompt | Ornith | Qwen3 |
|---|---|---|---|
| `wf-06` | "Weather in Boston in fahrenheit?" (3 tools) | ✅ `get_weather({city, unit})` | ✅ |
| `wf-07` | "Time in Tokyo?" (same 3 tools, expects `get_time`) | ✅ `get_time({city:"Tokyo"})` | ✅ |
| `wf-08` | Structured `create_ticket` with enum priority + array tags | ✅ correct enum + both tags | ✅ |

If you were going to bet on Ornith's agentic-RL training paying off *somewhere*, this was the axis — and it delivered, but Qwen3 delivered equally. No differentiator here on structured tool calls. Might still show up in longer agent loops (multi-turn tool use, error recovery), which this benchmark doesn't cover.

## Speed context (from bench outputs, not from prior sweep)

Both models generate at ~30 tok/s. Actual wall time per prompt is dominated by how many tokens each burns in `<think>`:

- On `wf-05` (concurrency): Qwen3 finished in 14s / 421 tok, Ornith took 28s / 847 tok. Qwen3 thought less.
- On `wf-04` (stack trace): Ornith 43s / 1274 tok, Qwen3 75s / 2162 tok. Ornith thought less.
- On `wf-02`, `wf-03`: **both** hit the 4096-token cap. This is the biggest surprise — even the 14B Q4 model can consume 4k tokens of thinking on a 20-line refactor prompt.

Ornith's 6× prompt-speed advantage from the speed benchmark **doesn't help here** because these prompts are short (~80 tokens). It would matter for long-context work (multi-file review, large system prompts).

## Reproducibility

Run against a live server on `http://127.0.0.1:8081/v1`:

```pwsh
python bench-coding.py --model ornith-9b --out bench-ornith-9b.json
python bench-coding.py --model qwen3-14b --out bench-qwen3-14b.json
```

Both benchmarks use identical sampling (temperature 0.2, seed 42, max_tokens 4096, one shot). Server config is the tuned config recorded in [`start-ornith-9b.ps1`](../start-ornith-9b.ps1) and [`start-qwen3-14b.ps1`](../start-qwen3-14b.ps1) — Q8 KV cache, `parallel 1`, `flash-attn on`, `--jinja` for tool calling.

## What this benchmark does not cover

- **Multi-turn / agent loops.** Tool-calling was single-shot; both models could fail in different ways over a 10-step agent trace.
- **Long-context.** Prompts topped out at ~80 tokens; Ornith's prompt-speed edge is invisible on short inputs.
- **Instruction-following without `<think>`.** Neither was tested with `/no_think`; a rerun in no-think mode might close Ornith's HumanEval gap (bypasses the loop failure mode) while hurting the debug prompts.
- **Non-Python languages.** Every code prompt except `wf-01` (PowerShell) is Python. Ornith's agentic-RL training was Python-heavy; other languages could shift the balance.
- **Latency to first token.** Both benches measure wall time to completion. Streaming latency profile is different and matters for interactive use.

## Turn-2 / turn-3 recovery — the self-correction Ornith is trained for

Ornith's RL training targets multi-turn self-correction, not single-shot pass@1. To test that, I fed each of the 3 HumanEval failures back as a turn-2 message with concrete feedback (system + original user prompt + Ornith's turn-1 reply + targeted feedback) and re-ran. See [`bench-recover.py`](../bench-recover.py) and [`bench-ornith-recover.json`](../bench-ornith-recover.json).

**Result: 2/3 on turn 2, third one recovered on turn 3 → 3/3 overall.**

| ID | Turn 1 | Feedback given on turn 2 | Turn-2 outcome | Turn-3 outcome |
|---|---|---|---|---|
| `he-03-truncate` | 4096 tok, empty content, `<think>` loop | "You didn't emit a function — keep `<think>` brief and just give me the definition." | **PASS** — 3.0s / 82 tok, clean `n - int(n)` | — |
| `he-06-intersperse` | Wrong algorithm `[1,4,4,2,3]` | Concrete test failure with expected vs actual | **PASS** — 14.2s / 416 tok, correct algorithm | — |
| `he-08-largest-prime-factor` | 4096 tok, empty content, `<think>` loop | "You didn't emit a function — keep `<think>` brief and just give me the definition." | FAIL — 7.3s / 215 tok, new algorithmic bug: returned `factor` after the loop, so `largest_prime_factor(27)` returned `5` instead of `3` | **PASS** — 14.2s / 419 tok, refactored to track `largest` alongside `factor` when given the concrete test failure |

**What this tells us:**

- **Reasoning-loop recovery is real.** Both `<think>`-loop failures broke out immediately on turn 2 when told to be brief — `he-03` went from 69s / 4096 tokens (`finish=length`, no output) to 3.0s / 82 tokens (`finish=stop`, correct). That's a 23× speedup and the difference between garbage and a working function.
- **Tool-feedback-style correction is real.** `he-06` and turn-3 `he-08` both got a concrete failing test case as feedback and correctly diagnosed + fixed the bug. This is the exact interaction pattern Ornith's RL training was designed for — and it works.
- **Turn-2 with "be brief" is a mixed feedback signal.** For `he-08`, the "keep `<think>` brief" prompt broke the loop but pushed Ornith into shipping code without adequate correctness checking. When the feedback is instead a specific test failure ("returned X, expected Y"), the model does the right thing. The lesson: telling Ornith *what's wrong* is better than telling it *to think less*.
- **The daily-driver verdict doesn't change.** Qwen3-14B still gets 10/10 in a single shot; Ornith needs 1–2 additional turns to hit the same result. In an interactive session that's fine; in an agent loop where each turn costs tokens and latency, single-shot pass@1 still matters.

**Where Ornith's differentiator would actually pay off:** an agent loop with real tool feedback (test runners, linters, subprocess output). This benchmark simulated that with hand-authored feedback strings; a real agent harness would produce equivalent signals automatically. If the workload is "code → run tests → fix", Ornith on turn 2+ is more competitive than the single-shot numbers suggest.

## Recommendation summary

**Keep Qwen3-14B Q4_K_L as the default local coding model** for single-shot / low-turn interactive coding. Ornith's turn-2 recovery is real but doesn't beat Qwen3's turn-1 correctness. Revisit Ornith when:
- A version ships with better `<think>` termination behavior (removing the need for the turn-2 "be brief" prompt), or
- A workload emerges that's dominated by long prompts (system prompt >8k, large context injection), where Ornith's 6× prompt-processing lead outweighs the quality gap, or
- An **agent-loop workload** materializes where tool output (test failures, linter errors) is fed back automatically — the domain Ornith's RL was tuned for and where it recovered 3/3 here, or
- VRAM pressure forces a smaller model.
