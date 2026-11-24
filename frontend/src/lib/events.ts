import { AuthError, authFetch, Message } from "./api";
import { parseSse } from "./sse";

type ConversationEvent = { type: "message"; message: Message };

function wait(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

export async function subscribeConversation(
  conversationId: string,
  onMessage: (message: Message) => void,
  signal: AbortSignal,
  onReconnect?: () => void | Promise<void>,
): Promise<void> {
  let backoff = 1000;
  let reconnecting = false;

  while (!signal.aborted) {
    try {
      const response = await authFetch(`/conversations/${conversationId}/events`, { signal });
      if (!response.ok || !response.body) {
        throw new Error(`Realtime connection failed (${response.status})`);
      }
      const connectedAt = Date.now();
      const isReconnect = reconnecting;
      reconnecting = true;

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      try {
        if (isReconnect) await onReconnect?.();
        while (!signal.aborted) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const parsed = parseSse(buffer);
          buffer = parsed.rest;
          for (const event of parsed.events) {
            if (event.event !== "message") continue;
            const data = event.data as ConversationEvent;
            if (data?.type === "message" && data.message?.id) {
              onMessage(data.message);
            }
          }
        }
      } finally {
        await reader.cancel().catch(() => undefined);
        reader.releaseLock();
      }
      if (Date.now() - connectedAt >= 10_000) backoff = 1000;
    } catch (error) {
      if (signal.aborted || (error instanceof DOMException && error.name === "AbortError")) return;
      if (error instanceof AuthError) throw error;
    }

    if (signal.aborted) return;
    reconnecting = true;
    try {
      await wait(backoff, signal);
    } catch {
      return;
    }
    backoff = Math.min(backoff * 2, 10_000);
  }
}
