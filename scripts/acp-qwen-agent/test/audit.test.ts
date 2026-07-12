import { describe, expect, it } from "vitest";
import { AuditLog } from "../src/agent/audit.js";

describe("AuditLog", () => {
  it("records metadata-only events", () => {
    const audit = new AuditLog();
    const e = audit.record({
      kind: "tool",
      ok: true,
      tool: "list_files",
      durationMs: 3,
      detail: "count=2",
      sessionId: "abc",
    });
    expect(e.ts).toBeTruthy();
    expect(audit.list()).toHaveLength(1);
    expect(audit.list()[0]?.tool).toBe("list_files");
  });
});
