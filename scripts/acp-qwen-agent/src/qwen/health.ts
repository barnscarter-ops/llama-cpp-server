import type { AppConfig } from "../config.js";
import { logError, logInfo } from "../logger.js";

export type ModelsListResponse = {
  data?: Array<{ id?: string }>;
};

export async function fetchModels(config: AppConfig): Promise<ModelsListResponse> {
  const url = `${config.baseUrl.replace(/\/$/, "")}/models`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), config.timeoutMs);
  try {
    const res = await fetch(url, {
      method: "GET",
      signal: controller.signal,
      headers: { accept: "application/json" },
    });
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new Error(
        `GET ${url} failed: HTTP ${res.status} ${res.statusText}${body ? ` — ${body.slice(0, 200)}` : ""}`,
      );
    }
    return (await res.json()) as ModelsListResponse;
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") {
      throw new Error(
        `GET ${url} timed out after ${config.timeoutMs}ms (guardian may be cold-starting or down)`,
      );
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

export async function runHealthCheck(config: AppConfig): Promise<number> {
  try {
    const models = await fetchModels(config);
    const ids = (models.data ?? [])
      .map((m) => m.id)
      .filter((id): id is string => typeof id === "string" && id.length > 0);

    if (ids.length === 0) {
      logError("health check failed: /v1/models returned no model ids", {
        baseUrl: config.baseUrl,
      });
      return 1;
    }

    const modelPresent = ids.includes(config.model);
    logInfo("health ok", {
      baseUrl: config.baseUrl,
      configuredModel: config.model,
      modelPresent,
      models: ids.join(","),
    });

    if (!modelPresent) {
      logError("configured ACP_QWEN_MODEL not present in /v1/models", {
        configuredModel: config.model,
        available: ids.join(","),
      });
      return 1;
    }

    return 0;
  } catch (err) {
    logError("health check failed", {
      message: err instanceof Error ? err.message : String(err),
      baseUrl: config.baseUrl,
    });
    return 1;
  }
}
