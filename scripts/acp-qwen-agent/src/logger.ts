/**
 * Stderr-only logger. Never write diagnostics to stdout (ACP wire).
 * Do not log prompts, API keys, or full file contents by default.
 */
export type LogFields = Record<string, string | number | boolean | null | undefined>;

function formatFields(fields?: LogFields): string {
  if (!fields || Object.keys(fields).length === 0) return "";
  const parts = Object.entries(fields)
    .filter(([, v]) => v !== undefined)
    .map(([k, v]) => `${k}=${String(v)}`);
  return parts.length ? ` ${parts.join(" ")}` : "";
}

export function logInfo(message: string, fields?: LogFields): void {
  console.error(`[acp-qwen] info ${message}${formatFields(fields)}`);
}

export function logWarn(message: string, fields?: LogFields): void {
  console.error(`[acp-qwen] warn ${message}${formatFields(fields)}`);
}

export function logError(message: string, fields?: LogFields): void {
  console.error(`[acp-qwen] error ${message}${formatFields(fields)}`);
}
