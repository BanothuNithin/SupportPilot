import { authFetch } from "./api";

export type PendingAction = {
  id: string;
  ticket_id: string | null;
  conversation_id: string;
  tool: string;
  arguments: Record<string, unknown>;
  status: "pending" | "approved" | "executing" | "executed" | "failed" | "rejected";
  result: Record<string, unknown> | null;
  error: string | null;
  edited: boolean;
  created_at: string;
  decided_at?: string | null;
};

async function responseError(response: Response): Promise<Error> {
  let message = `Request failed (${response.status})`;
  try {
    const data = await response.json();
    if (typeof data.detail === "string") message = data.detail;
    else if (data.detail) message = JSON.stringify(data.detail);
  } catch {
    // Keep the status message when the response is not JSON.
  }
  return new Error(message);
}

export async function listActions(options: {
  status?: string;
  ticketId?: string;
  conversationId?: string;
} = {}): Promise<PendingAction[]> {
  const query = new URLSearchParams();
  if (options.status) query.set("status", options.status);
  if (options.ticketId) query.set("ticket_id", options.ticketId);
  if (options.conversationId) query.set("conversation_id", options.conversationId);
  const suffix = query.size ? `?${query.toString()}` : "";
  const response = await authFetch(`/actions${suffix}`);
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function approveAction(
  id: string,
  argumentsValue: Record<string, unknown> | null,
): Promise<PendingAction> {
  const response = await authFetch(`/actions/${id}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ arguments: argumentsValue }),
  });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function rejectAction(id: string): Promise<PendingAction> {
  const response = await authFetch(`/actions/${id}/reject`, { method: "POST" });
  if (!response.ok) throw await responseError(response);
  return response.json();
}
