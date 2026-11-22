import { authFetch } from "./api";

export type Ticket = {
  id: string;
  conversation_id: string;
  title: string;
  priority: "low" | "medium" | "high";
  status: "open" | "in_progress" | "resolved";
  reason: string | null;
  created_at: string;
};

export async function listTickets(): Promise<Ticket[]> {
  const r = await authFetch("/tickets");
  if (!r.ok) throw new Error(`Request failed (${r.status})`);
  return r.json();
}

export async function setTicketStatus(id: string, status: Ticket["status"]): Promise<Ticket> {
  const r = await authFetch(`/tickets/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  if (!r.ok) throw new Error(`Request failed (${r.status})`);
  return r.json();
}
