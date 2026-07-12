import { spawn } from "node:child_process";
import { z } from "zod";
import { resolveInsideWorkspace, toWorkspaceRelative } from "./path_guard.js";
import {
  MAX_SEARCH_MATCHES,
  truncateOutput,
  type ToolContext,
  type ToolDefinition,
  type ToolResult,
} from "./types.js";

const Params = z.object({
  pattern: z.string().min(1).max(200),
  path: z.string().default("."),
});

function runRg(
  args: string[],
  cwd: string,
): Promise<{ code: number | null; stdout: string; stderr: string }> {
  return new Promise((resolve, reject) => {
    const child = spawn("rg", args, {
      cwd,
      windowsHide: true,
      shell: false,
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (c) => {
      stdout += c.toString("utf8");
      if (stdout.length > 200_000) stdout = stdout.slice(0, 200_000);
    });
    child.stderr.on("data", (c) => {
      stderr += c.toString("utf8");
    });
    child.on("error", (err) => reject(err));
    child.on("close", (code) => resolve({ code, stdout, stderr }));
  });
}

async function execute(args: unknown, ctx: ToolContext): Promise<ToolResult> {
  const parsed = Params.safeParse(args ?? {});
  if (!parsed.success) {
    return { ok: false, output: `invalid args: ${parsed.error.message}` };
  }
  const scope = resolveInsideWorkspace(ctx.workspaceRoot, parsed.data.path);
  const relScope = toWorkspaceRelative(ctx.workspaceRoot, scope).replaceAll(
    "\\",
    "/",
  );

  // Fixed safe args only — pattern is a search string, not a shell command.
  const rgArgs = [
    "--line-number",
    "--with-filename",
    "--no-heading",
    "--color",
    "never",
    "--max-count",
    String(MAX_SEARCH_MATCHES),
    "--glob",
    "!**/.git/**",
    "--glob",
    "!**/node_modules/**",
    "--glob",
    "!**/models/**",
    "--glob",
    "!**/logs/**",
    "--fixed-strings",
    parsed.data.pattern,
    scope,
  ];

  try {
    const { code, stdout, stderr } = await runRg(rgArgs, ctx.workspaceRoot);
    // rg: 0 matches, 1 no matches, 2 error
    if (code === 2) {
      return {
        ok: false,
        output: truncateOutput(`rg error: ${stderr || "unknown"}`),
      };
    }
    if (!stdout.trim()) {
      return {
        ok: true,
        output: `No matches for ${JSON.stringify(parsed.data.pattern)} under ${relScope}`,
      };
    }
    // Rewrite absolute paths to workspace-relative when possible.
    const lines = stdout
      .split(/\r?\n/)
      .filter(Boolean)
      .slice(0, MAX_SEARCH_MATCHES)
      .map((line) => {
        const idx = line.indexOf(":");
        if (idx === -1) return line;
        // filename:line:text — filename may contain drive letters on Windows
        const m = line.match(/^(.*?):(\d+):(.*)$/);
        if (!m) return line;
        const file = m[1]!;
        const ln = m[2]!;
        const text = m[3]!;
        try {
          const rel = toWorkspaceRelative(
            ctx.workspaceRoot,
            file,
          ).replaceAll("\\", "/");
          return `${rel}:${ln}:${text}`;
        } catch {
          return line;
        }
      });
    return { ok: true, output: truncateOutput(lines.join("\n")) };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    if (/ENOENT/i.test(message)) {
      return {
        ok: false,
        output:
          "rg (ripgrep) not found on PATH. Install ripgrep or ensure `rg` is available.",
      };
    }
    return { ok: false, output: `search failed: ${message}` };
  }
}

export const searchTextTool: ToolDefinition = {
  name: "search_text",
  description:
    "Search for a fixed string in the workspace using ripgrep (rg). No shell. Pattern is literal text.",
  parameters: Params,
  parametersJsonSchema: {
    type: "object",
    properties: {
      pattern: {
        type: "string",
        description: "Literal text to search for (fixed string, not regex)",
      },
      path: {
        type: "string",
        description: "Workspace-relative scope",
        default: ".",
      },
    },
    required: ["pattern"],
    additionalProperties: false,
  },
  execute,
};
