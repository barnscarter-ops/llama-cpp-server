// launch-llama.cjs — PM2 wrapper that guarantees llama-server.exe dies with it.
//
// Why: PM2's treekill/kill_timeout does NOT reliably terminate a native exe on
// Windows — observed 2026-08-07: `pm2 stop`/`pm2 restart` left llama-server.exe
// alive holding port 8081, so each respawn couldn't bind and crash-looped
// (687 restarts, two 35GB orphans pinning RAM). PM2 now manages this Node
// wrapper instead; Node child processes ARE killable by PM2, and the wrapper
// tree-kills the exe on every shutdown path via `taskkill /T /F`.
//
// Usage (from ecosystem.config.cjs):
//   script: this file, args: [<path-to-llama-server.exe>, ...server args]
// Requires shutdown_with_message: true in the PM2 app config so PM2 sends the
// IPC 'shutdown' message instead of a signal the exe never sees.
const { spawn, execSync } = require("child_process");

const [exe, ...args] = process.argv.slice(2);
if (!exe) {
  console.error("launch-llama: missing llama-server.exe path argument");
  process.exit(2);
}

// Defense-in-depth: before spawning, kill any stale llama-server already
// holding our port (leftover from a previous failed kill). Without this, a
// squatter makes every new instance exit on bind failure -> PM2 crash-loop.
function killStaleOnPort() {
  const i = args.indexOf("--port");
  const port = i >= 0 ? args[i + 1] : null;
  if (!port) return;
  let out = "";
  try {
    out = execSync(`netstat -ano -p tcp | findstr LISTENING | findstr ":${port} "`, { encoding: "utf8" });
  } catch {
    return; // no listener on the port — nothing to clear
  }
  for (const line of out.trim().split(/\r?\n/)) {
    const pid = line.trim().split(/\s+/).pop();
    if (!/^\d+$/.test(pid) || Number(pid) === 0) continue;
    try {
      const img = execSync(`tasklist /FI "PID eq ${pid}" /FO CSV /NH`, { encoding: "utf8" });
      if (/llama-server\.exe/i.test(img)) {
        console.log(`launch-llama: killing stale llama-server pid ${pid} squatting on port ${port}`);
        execSync(`taskkill /PID ${pid} /T /F`);
      } else {
        console.error(`launch-llama: port ${port} held by pid ${pid} (not llama-server) — refusing to kill, will fail to bind`);
      }
    } catch { /* process vanished between netstat and tasklist */ }
  }
}

killStaleOnPort();

const child = spawn(exe, args, { stdio: "inherit", windowsHide: true });
console.log(`launch-llama: spawned ${exe} as pid ${child.pid}`);

let exiting = false;
function treeKillChild() {
  try { execSync(`taskkill /PID ${child.pid} /T /F`, { stdio: "ignore" }); } catch { /* already dead */ }
}
function shutdown(code) {
  if (exiting) return;
  exiting = true;
  treeKillChild();
  process.exit(code);
}

child.on("exit", (code) => {
  if (!exiting) {
    exiting = true;
    process.exit(code ?? 1); // propagate so PM2 autorestart logic still works
  }
});
process.on("message", (m) => { if (m === "shutdown") shutdown(0); });
process.on("SIGINT", () => shutdown(0));
process.on("SIGTERM", () => shutdown(0));
process.on("exit", () => { if (!exiting) treeKillChild(); }); // last-resort sweep
