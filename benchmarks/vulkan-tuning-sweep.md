
## 2026-08-06 19:01 — Qwen3.6-35B-A3B-UD-Q5_K_M.gguf @ 32768 ctx

| Config | Prefill t/s | Generate t/s |
| --- | ---: | ---: |
| baseline (ub512, b2048, fa on, f16 kv) | 2260.7 | 111.9 |
| ub1024, b2048, fa on, f16 kv (prod) | 2238.9 | 112.1 |
| ub2048, b2048, fa on, f16 kv | 2198.9 | 110.1 |
| ub1024, b4096, fa on, f16 kv | 2203.1 | 108.7 |
| ub2048, b4096, fa on, f16 kv | 2194.2 | 108.6 |
| ub1024, b2048, fa OFF, f16 kv | 1362.5 | 97.1 |
| ub1024, b2048, fa on, q8_0 kv | 1523.9 | 111.4 |
| ub1024, b2048, fa on, q4_0 kv | 1563.2 | 113.4 |
| ub2048, b4096, fa on, q8_0 kv | 1536.9 | 111.2 |

