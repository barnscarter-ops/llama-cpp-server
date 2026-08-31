# llama-cpp-server

Local model serving stack for CMB-WorkBench (X870E / 9900X / 4060 Ti 16 GB).

Canonical path: D:\Workspace\Infrastructure\llama-cpp-server
(C:\Workspace is a junction to D:\Workspace.)

## Start here

1. AGENTS.md -- repo rules and session-close contract
2. docs/NEXT-SESSION.md -- live handoff (short)
3. memory/journal.md -- chronological log
4. qwen-queue/README.md -- guardian / queue / PM2 names and ports

Durable facts live in D:\Workspace\Active\brain, not extra files in this repo.

## Layout notes

- Runtime binaries PM2 actually runs: sibling llama-cpp-server-cuda-b10488 (do not treat repo-root llama-server.exe as live).
- Vulkan bins: sibling llama-cpp-server-vulkan (kept; used by bench scripts and current PM2 log paths).
- Nested product r9700-monitor/ is a separate git repo nested on disk. Do not git add it into this tree.

Do not create files or folders at the Workspace root. Do not git add -A.
