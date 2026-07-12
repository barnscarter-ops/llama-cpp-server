import fs from "node:fs";
import path from "node:path";
import { z } from "zod";
import {
  resolveInsideWorkspace,
  toWorkspaceRelative,
} from "./path_guard.js";
import {
  MAX_LIST_DEPTH,
  MAX_LIST_ENTRIES,
  truncateOutput,
  type ToolContext,
  type ToolDefinition,
  type ToolResult,
} from "./types.js";

const SKIP_DIR_NAMES = new Set([
  ".git",
  "node_modules",
  "models",
  "logs",
  "dist",
  "coverage",
]);

function isSkippedName(name: string): boolean {
  if (SKIP_DIR_NAMES.has(name)) return true;
  if (name === ".env" || name.startsWith(".env.")) return true;
  if (name.endsWith(".pem") || name.endsWith(".key")) return true;
  return false;
}

const Params = z.object({
  path: z.string().default("."),
  maxDepth: z.number().int().min(0).max(MAX_LIST_DEPTH).default(2),
});

async function execute(args: unknown, ctx: ToolContext): Promise<ToolResult> {
  const parsed = Params.safeParse(args ?? {});
  if (!parsed.success) {
    return { ok: false, output: `invalid args: ${parsed.error.message}` };
  }
  const start = resolveInsideWorkspace(ctx.workspaceRoot, parsed.data.path);
  const maxDepth = parsed.data.maxDepth;
  const lines: string[] = [];
  let count = 0;
  let truncated = false;

  function walk(dir: string, depth: number): void {
    if (truncated || count >= MAX_LIST_ENTRIES) {
      truncated = true;
      return;
    }
    let entries: fs.Dirent[];
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch (err) {
      lines.push(
        `! cannot read ${toWorkspaceRelative(ctx.workspaceRoot, dir)}: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
      return;
    }
    entries.sort((a, b) => a.name.localeCompare(b.name));
    for (const ent of entries) {
      if (truncated || count >= MAX_LIST_ENTRIES) {
        truncated = true;
        return;
      }
      if (isSkippedName(ent.name)) continue;
      const full = path.join(dir, ent.name);
      const rel = toWorkspaceRelative(ctx.workspaceRoot, full).replaceAll("\\", "/");
      if (ent.isDirectory()) {
        lines.push(`${rel}/`);
        count += 1;
        if (depth < maxDepth) walk(full, depth + 1);
      } else if (ent.isFile()) {
        lines.push(rel);
        count += 1;
      }
    }
  }

  const st = fs.statSync(start);
  if (st.isFile()) {
    const rel = toWorkspaceRelative(ctx.workspaceRoot, start).replaceAll("\\", "/");
    return { ok: true, output: truncateOutput(rel) };
  }
  walk(start, 0);
  const body =
    lines.join("\n") +
    (truncated ? `\n...[truncated at ${MAX_LIST_ENTRIES} entries]` : "");
  return { ok: true, output: truncateOutput(body || "(empty)") };
}

export const listFilesTool: ToolDefinition = {
  name: "list_files",
  description:
    "List files and directories under a workspace-relative path. Depth-capped; skips .git, node_modules, models, logs, secrets.",
  parameters: Params,
  parametersJsonSchema: {
    type: "object",
    properties: {
      path: {
        type: "string",
        description: "Workspace-relative or absolute-inside-workspace path",
        default: ".",
      },
      maxDepth: {
        type: "integer",
        minimum: 0,
        maximum: MAX_LIST_DEPTH,
        default: 2,
      },
    },
    additionalProperties: false,
  },
  execute,
};
