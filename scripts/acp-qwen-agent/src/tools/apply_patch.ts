import fs from "node:fs";
import pathModule from "node:path";
import crypto from "node:crypto";
import { z } from "zod";
import {
  resolveInsideWorkspace,
  toWorkspaceRelative,
} from "./path_guard.js";
import {
  MAX_TOOL_OUTPUT_CHARS,
  truncateOutput,
  type ToolContext,
  type ToolDefinition,
  type ToolResult,
} from "./types.js";
import { logInfo } from "../logger.js";

const Params = z.object({
  path: z.string().min(1),
  newContent: z.string(),
  description: z.string().min(1).max(1000).optional(),
});

function generateUnifiedDiff(
  filePath: string,
  oldContent: string,
  newContent: string,
): string {
  const oldLines = oldContent.split("\n");
  const newLines = newContent.split("\n");

  const m = oldLines.length;
  const n = newLines.length;

  const dp: number[][] = Array.from({ length: m + 1 }, () =>
    Array(n + 1).fill(0),
  );
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      if (oldLines[i - 1] === newLines[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }
  }

  let i = m;
  let j = n;
  const diffParts: string[] = [];

  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && oldLines[i - 1] === newLines[j - 1]) {
      diffParts.unshift(` ${oldLines[i - 1]}`);
      i -= 1;
      j -= 1;
    } else if (j > 0 && (i === 0 || dp[i][j] === dp[i][j - 1])) {
      diffParts.unshift(`+${newLines[j - 1]}`);
      j -= 1;
    } else if (i > 0) {
      diffParts.unshift(`-${oldLines[i - 1]}`);
      i -= 1;
    }
  }

  return diffParts.join("\n");
}

export function computeDiffHash(path: string, newContent: string): string {
  const sha = crypto.createHash("sha256");
  const payload = `${path}:${newContent}`;
  sha.update(payload);
  return sha.digest("hex");
}

export function executePatchWrite(
  path: string,
  newContent: string,
  workspaceRoot: string,
): ToolResult {
  const abs = resolveInsideWorkspace(workspaceRoot, path);
  const rel = toWorkspaceRelative(workspaceRoot, abs).replaceAll("\\", "/");

  try {
    fs.mkdirSync(pathModule.dirname(abs), { recursive: true });
    fs.writeFileSync(abs, newContent, "utf8");
    logInfo("apply_patch wrote", { path: rel });
    return {
      ok: true,
      output: `Wrote ${rel}`,
    };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return { ok: false, output: `write failed: ${message}` };
  }
}

export type WriteApproval = {
  diffHash: string;
  approved: boolean;
  timestamp: string;
};

export class WriteApprovalStore {
  private approvals = new Map<string, WriteApproval>();

  set(path: string, newContent: string, approved: boolean): void {
    const hash = this.computeHash(path, newContent);
    this.approvals.set(hash, {
      diffHash: hash,
      approved,
      timestamp: new Date().toISOString(),
    });
  }

  getApproval(path: string, newContent: string): WriteApproval | undefined {
    const hash = this.computeHash(path, newContent);
    return this.approvals.get(hash);
  }

  private computeHash(path: string, newContent: string): string {
    const sha = crypto.createHash("sha256");
    const payload = `${path}:${newContent}`;
    sha.update(payload);
    return sha.digest("hex");
  }
}

// Module-level approval store for the agent instance
export const writeApprovalStore: WriteApprovalStore = new WriteApprovalStore();

async function execute(
  args: unknown,
  ctx: ToolContext,
): Promise<ToolResult> {
  const parsed = Params.safeParse(args ?? {});
  if (!parsed.success) {
    return { ok: false, output: `invalid args: ${parsed.error.message}` };
  }

  const abs = resolveInsideWorkspace(ctx.workspaceRoot, parsed.data.path);
  const rel = toWorkspaceRelative(ctx.workspaceRoot, abs).replaceAll("\\", "/");

  try {
    fs.mkdirSync(pathModule.dirname(abs), { recursive: true });
    fs.writeFileSync(abs, parsed.data.newContent, "utf8");
    return {
      ok: true,
      output: `Wrote ${rel}`,
    };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return { ok: false, output: `write failed: ${message}` };
  }
}

export const applyPatchTool: ToolDefinition = {
  name: "apply_patch",
  description:
    "Write a file to disk. Requires ACP_ALLOW_WRITES=true AND explicit editor approval for the exact diff hash.",
  parameters: Params,
  parametersJsonSchema: {
    type: "object",
    properties: {
      path: {
        type: "string",
        description: "Workspace-relative path to the file",
      },
      newContent: {
        type: "string",
        description: "The new file content to write",
      },
      description: {
        type: "string",
        description: "Optional short description of the change (max 1000 chars)",
        maxLength: 1000,
      },
    },
    required: ["path", "newContent"],
    additionalProperties: false,
  },
  execute,
};
