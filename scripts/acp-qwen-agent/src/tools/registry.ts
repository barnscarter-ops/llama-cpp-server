import { listFilesTool } from "./list_files.js";
import { readFileTool } from "./read_file.js";
import { searchTextTool } from "./search_text.js";
import { proposePatchTool } from "./propose_patch.js";
import type { ToolContext, ToolDefinition, ToolResult } from "./types.js";
import { PathGuardError } from "./path_guard.js";
import { truncateOutput } from "./types.js";

const tools: ToolDefinition[] = [
  listFilesTool,
  readFileTool,
  searchTextTool,
  proposePatchTool,
];

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
