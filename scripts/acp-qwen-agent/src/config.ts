import { z } from "zod";
import path from "node:path";

const ConfigSchema = z.object({
  baseUrl: z
    .string()
    .url()
    .default("http://127.0.0.1:8080/v1"),
  model: z.string().min(1).default("qwen3.6-35b"),
  workspace: z
    .string()
    .optional()
    .transform((v) => (v && v.trim() ? path.resolve(v.trim()) : undefined)),
  timeoutMs: z.coerce.number().int().positive().default(120_000),
  allowWrites: z
    .union([z.boolean(), z.string()])
    .default(false)
    .transform((v) => {
      if (typeof v === "boolean") return v;
      const s = v.trim().toLowerCase();
      return s === "1" || s === "true" || s === "yes";
    }),
});

export type AppConfig = z.infer<typeof ConfigSchema> & {
  workspace?: string;
  allowWrites: boolean;
};

export function loadConfig(env: NodeJS.ProcessEnv = process.env): AppConfig {
  const parsed = ConfigSchema.safeParse({
    baseUrl: env.ACP_QWEN_BASE_URL,
    model: env.ACP_QWEN_MODEL,
    workspace: env.ACP_WORKSPACE,
    timeoutMs: env.ACP_QWEN_TIMEOUT_MS,
    allowWrites: env.ACP_ALLOW_WRITES,
  });

  if (!parsed.success) {
    const detail = parsed.error.issues
      .map((i) => `${i.path.join(".") || "(root)"}: ${i.message}`)
      .join("; ");
    throw new Error(`Invalid configuration: ${detail}`);
  }

  return parsed.data as AppConfig;
}

export function requireWorkspace(config: AppConfig): string {
  if (!config.workspace) {
    throw new Error(
      "ACP_WORKSPACE is required for this command (absolute path to the workspace root)",
    );
  }
  return config.workspace;
}
