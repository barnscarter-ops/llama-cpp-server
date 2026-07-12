import { listFilesTool } from "./list_files.js";
import { readFileTool } from "./read_file.js";
import { searchTextTool } from "./search_text.js";
import { proposePatchTool } from "./propose_patch.js";
import {
  applyPatchTool,
  computeDiffHash,
  writeApprovalStore,
} from "./apply_patch.js";
import type { ToolContext, ToolDefinition, ToolResult } from "./types.js";
import { PathGuardError } from "./path_guard.js";
import { truncateOutput } from "./types.js";
import { logInfo } from "../logger.js";

const tools: ToolDefinition[] = [
  listFilesTool,
  readFileTool,
  searchTextTool,
  proposePatchTool,
  applyPatchTool,
];

export { writeApprovalStore };

let allowWritesConfig = false;

export function setAllowWrites(value: boolean): void {
  allowWritesConfig = value;
}

export function getAllowWrites(): boolean {
  return allowWritesConfig;
}

export function getToolDefinitions(): ToolDefinition[] {
  return tools;
}

export function getOpenAiToolSpecs(): Array<{
  type: "function";
  function: {
    name: string;
    description: string;
    parameters: Record<string, unknown>;
  };
}> {
  return tools.map((t) => ({
    type: "function" as const,
    function: {
      name: t.name,
      description: t.description,
      parameters: t.parametersJsonSchema,
    },
  }));
}

export async function executeTool(
  name: string,
  args: unknown,
  ctx: ToolContext,
): Promise<ToolResult> {
  const tool = tools.find((t) => t.name === name);
  if (!tool) {
    return { ok: false, output: `unknown tool: ${name}` };
  }

  // Dual gate for apply_patch
  if (name === "apply_patch") {
    // Gate 1: ACP_ALLOW_WRITES
    if (!allowWritesConfig) {
      return {
        ok: false,
        output:
          "apply_patch is disabled. Set ACP_ALLOW_WRITES=true to enable writes.",
      };
    }

    // Parse args to compute diff hash
    let parsedArgs: { path: string; newContent: string } | null = null;
    try {
      const raw = typeof args === "string" ? JSON.parse(args) : args;
      if (raw && typeof raw === "object") {
        parsedArgs = { path: raw.path ?? "", newContent: raw.newContent ?? "" };
      }
    } catch {
      // args wasn't JSON — will get JSON parse error below
    }

    if (parsedArgs && typeof parsedArgs.path === "string") {
      const diffHash = computeDiffHash(
        parsedArgs.path,
        parsedArgs.newContent ?? "",
      );

      // Gate 2: editor approval for exact diff hash
      const approval = writeApprovalStore.getApproval(
        parsedArgs.path,
        parsedArgs.newContent,
      );

      if (!approval || !approval.approved) {
        return {
          ok: false,
          output:
            `apply_patch rejected: no editor approval for diff hash ${diffHash.slice(0, 16)}.... Requires explicit ACP session/request_permission for this exact hash.`,
        };
      }
    }
  }

  try {
    const result = await tool.execute(args, ctx);
    return {
      ok: result.ok,
      output: truncateOutput(result.output),
    };
  } catch (err) {
    if (err instanceof PathGuardError) {
      return { ok: false, output: `path rejected: ${err.message}` };
    }
    return {
      ok: false,
      output: err instanceof Error ? err.message : String(err),
    };
  }
}
