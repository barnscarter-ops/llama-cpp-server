import { logInfo } from "../logger.js";

export type AuditEvent = {
  ts: string;
  kind: string;
  ok: boolean;
  tool?: string;
  durationMs?: number;
  detail?: string;
  sessionId?: string;
};

/**
 * Metadata-only audit trail. Never store prompts, file contents, or secrets.
 */
export class AuditLog {
  private readonly events: AuditEvent[] = [];

  record(event: Omit<AuditEvent, "ts"> & { ts?: string }): AuditEvent {
    const full: AuditEvent = {
      ts: event.ts ?? new Date().toISOString(),
      kind: event.kind,
      ok: event.ok,
      tool: event.tool,
      durationMs: event.durationMs,
      detail: event.detail,
      sessionId: event.sessionId,
    };
    this.events.push(full);
    logInfo("audit", {
      kind: full.kind,
      ok: full.ok,
      tool: full.tool ?? "",
      durationMs: full.durationMs ?? -1,
      detail: full.detail ?? "",
      sessionId: full.sessionId ?? "",
    });
    return full;
  }

  list(): readonly AuditEvent[] {
    return this.events;
  }
}
