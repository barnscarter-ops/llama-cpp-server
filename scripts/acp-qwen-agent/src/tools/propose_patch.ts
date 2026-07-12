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

  // Simple LCS-based unified diff
  const m = oldLines.length;
  const n = newLines.length;

  // Build LCS table
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

  // Backtrack to find diff
  let i = m;
  let j = n;
  const diffParts: string[] = [];

  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && oldLines[i - 1] === newLines[j - 1]) {
      // Context line
      diffParts.unshift(` ${oldLines[i - 1]}`);
      i -= 1;
      j -= 1;
    } else if (j > 0 && (i === 0 || dp[i][j] === dp[i][j - 1])) {
      // Added line
      diffParts.unshift(`+${newLines[j - 1]}`);
      j -= 1;
    } else if (i > 0) {
      // Removed line
      diffParts.unshift(`-${oldLines[i - 1]}`);
      i -= 1;
    }
  }

  return diffParts.join("\n");
}

async function execute(args: unknown, ctx: ToolContext): Promise<ToolResult> {
  const parsed = Params.safeParse(args ?? {});
  if (!parsed.success) {
    return { ok: false, output: `invalid args: ${parsed.error.message}` };
  }

  const abs = resolveInsideWorkspace(ctx.workspaceRoot, parsed.data.path);
  const rel = toWorkspaceRelative(ctx.workspaceRoot, abs).replaceAll("\\", "/");

  // Read current file content
  let oldContent = "";
  try {
    const { default: fs } = await import("node:fs");
    const buf = fs.readFileSync(abs, "utf8");
    oldContent = buf;
  } catch {
    oldContent = ""; // New file — nothing to diff against
  }

  const diff = generateUnifiedDiff(rel, oldContent, parsed.data.newContent);
  const desc = parsed.data.description ? ` (${parsed.data.description})` : "";

  const output = `--- ${rel} (proposed diff)${desc}\n${diff}`;
  return {
    ok: true,
    output: truncateOutput(output, MAX_TOOL_OUTPUT_CHARS),
  };
}

export const proposePatchTool: ToolDefinition = {
  name: "propose_patch",
  description:
    "Generate a unified diff (in-memory only, no disk write). Shows the proposed change for editor review.",
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
        description: "The new file content to diff against the existing",
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
