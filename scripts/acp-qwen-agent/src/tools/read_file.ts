import fs from "node:fs";
import { z } from "zod";
import { resolveInsideWorkspace, toWorkspaceRelative } from "./path_guard.js";
import {
  MAX_READ_BYTES,
  truncateOutput,
  type ToolContext,
  type ToolDefinition,
  type ToolResult,
} from "./types.js";

const Params = z.object({
  path: z.string().min(1),
});

function looksBinary(buf: Buffer): boolean {
  const sample = buf.subarray(0, Math.min(buf.length, 8000));
  if (sample.includes(0)) return true;
  let weird = 0;
  for (const b of sample) {
    if (b === 9 || b === 10 || b === 13) continue;
    if (b < 32 || b === 127) weird += 1;
  }
  return weird / Math.max(sample.length, 1) > 0.3;
}

async function execute(args: unknown, ctx: ToolContext): Promise<ToolResult> {
  const parsed = Params.safeParse(args ?? {});
  if (!parsed.success) {
    return { ok: false, output: `invalid args: ${parsed.error.message}` };
  }
  const abs = resolveInsideWorkspace(ctx.workspaceRoot, parsed.data.path);
  const st = fs.statSync(abs);
  if (!st.isFile()) {
    return { ok: false, output: "not a regular file" };
  }
  if (st.size > MAX_READ_BYTES) {
    return {
      ok: false,
      output: `file too large (${st.size} bytes > ${MAX_READ_BYTES})`,
    };
  }
  const buf = fs.readFileSync(abs);
  if (looksBinary(buf)) {
    return { ok: false, output: "binary file rejected (UTF-8 text only)" };
  }
  const text = buf.toString("utf8");
  const rel = toWorkspaceRelative(ctx.workspaceRoot, abs).replaceAll("\\", "/");
  return {
    ok: true,
    output: truncateOutput(`# ${rel}\n${text}`),
  };
}

export const readFileTool: ToolDefinition = {
  name: "read_file",
  description:
    "Read a UTF-8 text file inside the workspace. Rejects binary and oversized files.",
  parameters: Params,
  parametersJsonSchema: {
    type: "object",
    properties: {
      path: {
        type: "string",
        description: "Workspace-relative path to read",
      },
    },
    required: ["path"],
    additionalProperties: false,
  },
  execute,
};
