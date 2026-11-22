import { parseSse } from "./sse";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const KEY = "supportpilot.tokens";

type Tokens = { access_token: string; refresh_token: string };
export type Conversation = { id: string; customer_id: string; status: string; created_at: string };
export type Citation = {
  n: number;
  document_id: string;
  filename: string;
  chunk_index: number;
  score: number;
  snippet: string;
};
export type Message = {
  id: string;
  sender: "customer" | "ai" | "human";
  content: string;
  created_at?: string;
  citations?: Citation[] | null;
};

export class AuthError extends Error {}

// ---- token storage (localStorage for now; see DECISIONS.md for the trade-off) ----
export function getTokens(): Tokens | null {
  if (typeof window === "undefined") return null;
  const raw = localStorage.getItem(KEY);
  return raw ? (JSON.parse(raw) as Tokens) : null;
}
function saveTokens(t: Tokens) {
  localStorage.setItem(KEY, JSON.stringify(t));
}
function clearTokens() {
  localStorage.removeItem(KEY);
}

// ---- auth ----
export async function login(email: string, password: string) {
  const r = await fetch(`${API}/api/v1/auth/login`, {
    method: "POST",
    body: new URLSearchParams({ username: email, password }), // the login route expects a form, not JSON
  });
  if (r.status === 429) throw new Error("Too many attempts. Wait a minute and try again.");
  if (!r.ok) throw new Error("Incorrect email or password.");
  saveTokens(await r.json());
}

export async function register(company: string, email: string, password: string) {
  const r = await fetch(`${API}/api/v1/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ company_name: company, email, password }),
  });
  if (r.status === 409) throw new Error("That email is already registered.");
  if (!r.ok) throw new Error("Could not register. Check the fields (password needs 8+ characters).");
  saveTokens(await r.json());
}

export async function logout() {
  const t = getTokens();
  if (t) {
    await fetch(`${API}/api/v1/auth/logout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: t.refresh_token }),
    }).catch(() => {});
  }
  clearTokens();
}

// Refresh tokens are single-use (rotation). If two requests refresh at once, the second would fail
// and log the user out, so all callers share ONE in-flight refresh.
let refreshing: Promise<boolean> | null = null;
function refreshTokens(): Promise<boolean> {
  if (!refreshing) {
    refreshing = (async () => {
      const t = getTokens();
      if (!t) return false;
      const r = await fetch(`${API}/api/v1/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: t.refresh_token }),
      });
      if (!r.ok) {
        clearTokens();
        return false;
      }
      saveTokens(await r.json());
      return true;
    })().finally(() => {
      refreshing = null;
    });
  }
  return refreshing;
}

// fetch with the access token attached; on 401 refresh once and retry
export async function authFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const send = () => {
    const t = getTokens();
    if (!t) throw new AuthError("Not logged in");
    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${t.access_token}`);
    return fetch(`${API}/api/v1${path}`, { ...init, headers });
  };
  let r = await send();
  if (r.status === 401) {
    if (!(await refreshTokens())) throw new AuthError("Session expired");
    r = await send();
  }
  return r;
}

async function json<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`Request failed (${r.status})`);
  return (await r.json()) as T;
}

// ---- data ----
export async function listConversations(): Promise<Conversation[]> {
  return json(await authFetch("/conversations"));
}

export async function getMessages(id: string): Promise<Message[]> {
  const conv = await json<{ messages: Message[] }>(await authFetch(`/conversations/${id}`));
  return conv.messages;
}

// Staff test-chat needs a customer. Reuse the first one, or create a demo customer.
export async function createConversation(): Promise<Conversation> {
  const customers = await json<{ id: string }[]>(await authFetch("/customers"));
  const customerId =
    customers[0]?.id ??
    (
      await json<{ id: string }>(
        await authFetch("/customers", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ external_id: "demo-customer" }),
        }),
      )
    ).id;
  return json(
    await authFetch("/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ customer_id: customerId }),
    }),
  );
}

// ---- streaming ----
// EventSource cannot send POST bodies or an Authorization header, so we read the stream manually.
export async function streamMessage(
  conversationId: string,
  content: string,
  onToken: (text: string) => void,
  signal?: AbortSignal,
  onCitations?: (items: Citation[]) => void,
  onEvent?: (event: string, data: Record<string, unknown>) => void,
) {
  const r = await authFetch(`/conversations/${conversationId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
    signal,
  });
  if (!r.ok || !r.body) throw new Error(`Request failed (${r.status})`);

  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { events, rest } = parseSse(buffer);
    buffer = rest;
    for (const e of events) {
      const data = e.data as Record<string, unknown>;
      if (e.event === "token") {
        if (typeof data.text === "string") onToken(data.text);
        continue;
      }
      if (e.event === "citations") {
        if (Array.isArray(data.items)) onCitations?.(data.items as Citation[]);
        continue;
      }
      onEvent?.(e.event, data);
      if (e.event === "error") {
        throw new Error(typeof data.detail === "string" ? data.detail : "The AI request failed.");
      }
    }
  }
}
