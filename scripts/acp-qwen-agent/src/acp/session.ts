export type AgentSession = {
  pendingPrompt: AbortController | null;
};

export class SessionStore {
  private readonly sessions = new Map<string, AgentSession>();

  create(): string {
    const sessionId = Array.from(crypto.getRandomValues(new Uint8Array(16)))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
    this.sessions.set(sessionId, { pendingPrompt: null });
    return sessionId;
  }

  get(sessionId: string): AgentSession | undefined {
    return this.sessions.get(sessionId);
  }

  cancel(sessionId: string): void {
    this.sessions.get(sessionId)?.pendingPrompt?.abort();
  }
}
