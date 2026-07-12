import { z } from "zod";

export const MAX_TOOL_OUTPUT_CHARS = 24_000;
export const MAX_READ_BYTES = 256_000;
export const MAX_LIST_ENTRIES = 500;
export const MAX_LIST_DEPTH = 4;
export const MAX_SEARCH_MATCHES = 50;

export type ToolResult = {
  ok: boolean;
  output: string;
};

export type ToolContext = {
  workspaceRoot: string;
};

export type ToolDefinition = {
  name: string;
  description: string;
  parameters: z.ZodTypeAny;
  /** OpenAI-style JSON schema parameters object */
  parametersJsonSchema: Record<string, unknown>;
  execute: (args: unknown, ctx: ToolContext) => Promise<ToolResult>;
};

export function truncateOutput(text: string, max = MAX_TOOL_OUTPUT_CHARS): string {
  if (text.length <= max) return text;
  return `${text.slice(0, max)}\n...[truncated ${text.length - max} chars]`;
}
