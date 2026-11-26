"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import {
  AuthError,
  authFetch,
  Conversation,
  getCurrentUser,
  getMessages,
  getTokens,
  Message,
  postHumanReply,
  StaffUser,
} from "@/lib/api";
import { approveAction, listActions, PendingAction, rejectAction } from "@/lib/actions";
import { subscribeConversation } from "@/lib/events";
import { Ticket, listTickets, setTicketStatus } from "@/lib/tickets";

type ConversationDetail = Conversation & { messages: Message[] };
type ActionDraft = { title?: string; body?: string; text?: string };

const actionBadge: Record<PendingAction["status"], string> = {
  pending: "bg-amber-900/60 text-amber-300",
  approved: "bg-blue-900/60 text-blue-300",
  executing: "bg-violet-900/60 text-violet-300",
  executed: "bg-emerald-900/60 text-emerald-300",
  failed: "bg-red-900/60 text-red-300",
  rejected: "bg-slate-800 text-slate-300",
};

function messageStyle(sender: Message["sender"]) {
  if (sender === "customer") return "mr-auto border-slate-700 bg-slate-900 text-slate-100";
  if (sender === "human") return "ml-auto border-violet-700 bg-violet-950/70 text-violet-100";
  return "mr-auto border-slate-800 bg-slate-800/70 text-slate-300";
}

async function responseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") message = body.detail;
      else if (body.detail) message = JSON.stringify(body.detail);
    } catch {
      // Keep the HTTP status when the server response is not JSON.
    }
    throw new Error(message);
  }
  return response.json();
}

function ConversationStaffPage() {
  const { conversationId } = useParams<{ conversationId: string }>();
  const router = useRouter();
  const [conversation, setConversation] = useState<ConversationDetail | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [ticket, setTicket] = useState<Ticket | null>(null);
  const [actions, setActions] = useState<PendingAction[]>([]);
  const [user, setUser] = useState<StaffUser | null>(null);
  const [drafts, setDrafts] = useState<Record<string, ActionDraft>>({});
  const [reply, setReply] = useState("");
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [busyTicket, setBusyTicket] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");

  const handleError = useCallback((reason: unknown) => {
    if (reason instanceof AuthError) router.replace("/login");
    else setError(reason instanceof Error ? reason.message : "Something went wrong.");
  }, [router]);

  const mergeMessages = useCallback((incoming: Message[]) => {
    setMessages((current) => {
      const byId = new Map(current.map((message) => [message.id, message]));
      for (const message of incoming) byId.set(message.id, message);
      return [...byId.values()].sort((a, b) =>
        (a.created_at ?? "").localeCompare(b.created_at ?? ""),
      );
    });
  }, []);

  const refreshActions = useCallback(async () => {
    const matching = await listActions({ conversationId });
    setActions(matching);
    setDrafts((existing) => {
      const next = { ...existing };
      for (const action of matching) {
        if (next[action.id]) continue;
        const args = action.arguments;
        next[action.id] = {
          ...(typeof args.title === "string" ? { title: args.title } : {}),
          ...(typeof args.body === "string" ? { body: args.body } : {}),
          ...(typeof args.text === "string" ? { text: args.text } : {}),
        };
      }
      return next;
    });
  }, [conversationId]);

  const hasActionInProgress = actions.some(
    (action) => action.status === "approved" || action.status === "executing",
  );

  useEffect(() => {
    if (!hasActionInProgress) return;
    let refreshing = false;
    const timer = setInterval(() => {
      if (refreshing) return;
      refreshing = true;
      void refreshActions()
        .catch(handleError)
        .finally(() => {
          refreshing = false;
        });
    }, 2000);
    return () => clearInterval(timer);
  }, [hasActionInProgress, refreshActions, handleError]);

  const refresh = useCallback(async () => {
    const [convResponse, tickets, staff] = await Promise.all([
      fetchConversation(conversationId),
      listTickets(),
      getCurrentUser(),
    ]);
    setConversation(convResponse);
    mergeMessages(convResponse.messages);
    setTicket(tickets.find((item) => item.conversation_id === conversationId) ?? null);
    setUser(staff);
    await refreshActions();
  }, [conversationId, mergeMessages, refreshActions]);

  useEffect(() => {
    if (!getTokens()) {
      router.replace("/login");
      return;
    }
    (async () => {
      try {
        await refresh();
      } catch (reason) {
        handleError(reason);
      }
    })();
  }, [router, refresh, handleError]);

  useEffect(() => {
    if (!conversationId || !getTokens()) return;
    const controller = new AbortController();
    (async () => {
      try {
        await subscribeConversation(
          conversationId,
          (message) => {
            if (!controller.signal.aborted) mergeMessages([message]);
          },
          controller.signal,
          async () => {
            const latest = await getMessages(conversationId);
            if (!controller.signal.aborted) mergeMessages(latest);
          },
        );
      } catch (reason) {
        if (!controller.signal.aborted) handleError(reason);
      }
    })();
    return () => controller.abort();
  }, [conversationId, mergeMessages, handleError]);

  async function sendReply(event: React.FormEvent) {
    event.preventDefault();
    if (!conversation || conversation.status !== "escalated" || !reply.trim() || sending) return;
    setSending(true);
    setError("");
    try {
      const message = await postHumanReply(conversationId, reply.trim());
      mergeMessages([message]);
      setReply("");
    } catch (reason) {
      handleError(reason);
    } finally {
      setSending(false);
    }
  }

  async function updateTicketStatus(status: Ticket["status"]) {
    if (!ticket) return;
    setBusyTicket(true);
    setError("");
    try {
      setTicket(await setTicketStatus(ticket.id, status));
      if (status === "resolved") {
        setConversation((current) => current ? { ...current, status: "resolved" } : current);
      }
    } catch (reason) {
      handleError(reason);
    } finally {
      setBusyTicket(false);
    }
  }

  async function approve(action: PendingAction) {
    if (busyAction) return;
    if (
      action.status === "failed" &&
      action.error?.startsWith("Outcome unknown") &&
      !window.confirm(
        "This action may already have run. Check GitHub or Slack first. Retry anyway?",
      )
    ) {
      return;
    }
    setBusyAction(action.id);
    setError("");
    const draft = drafts[action.id] ?? {};
    const args = action.tool === "create_github_issue"
      ? { title: draft.title ?? "", body: draft.body ?? "", labels: action.arguments.labels ?? [] }
      : { text: draft.text ?? "" };
    const original = action.arguments;
    const unchanged = action.tool === "create_github_issue"
      ? args.title === original.title && args.body === original.body
      : args.text === original.text;
    try {
      const updated = await approveAction(action.id, unchanged ? null : args);
      setActions((current) => current.map((item) => item.id === action.id ? updated : item));
    } catch (reason) {
      handleError(reason);
    } finally {
      setBusyAction(null);
    }
  }

  async function reject(action: PendingAction) {
    if (busyAction) return;
    setBusyAction(action.id);
    setError("");
    try {
      const updated = await rejectAction(action.id);
      setActions((current) => current.map((item) => item.id === action.id ? updated : item));
    } catch (reason) {
      handleError(reason);
    } finally {
      setBusyAction(null);
    }
  }

  const canApprove = user?.role === "owner" || user?.role === "admin";
  const replyEnabled = conversation?.status === "escalated";

  return (
    <main className="min-h-screen bg-slate-950 p-4 text-slate-100 sm:p-6">
      <div className="mx-auto max-w-5xl space-y-5">
        <header className="flex items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold">Conversation</h1>
            <p className="break-all text-xs text-slate-500">{conversationId}</p>
          </div>
          <Link href="/inbox" className="text-sm text-slate-400 hover:text-white">Back to inbox</Link>
        </header>
        {error && <p role="alert" className="rounded-lg border border-red-900 bg-red-950/50 p-3 text-sm text-red-300">{error}</p>}

        <div className="grid gap-5 lg:grid-cols-[minmax(0,1.5fr)_minmax(18rem,1fr)]">
          <section className="flex min-h-[32rem] flex-col rounded-2xl border border-slate-800 bg-slate-900/40">
            <div className="flex items-center justify-between border-b border-slate-800 p-4">
              <h2 className="font-semibold">Messages</h2>
              <span className={`rounded-full px-2 py-1 text-xs ${replyEnabled ? "bg-amber-900/60 text-amber-300" : "bg-slate-800 text-slate-300"}`}>
                {conversation?.status ?? "Loading"}
              </span>
            </div>
            <div className="flex-1 space-y-3 overflow-y-auto p-4">
              {messages.map((message) => (
                <article key={message.id} className={`max-w-[90%] rounded-xl border p-3 ${messageStyle(message.sender)}`}>
                  {message.sender === "human" && <p className="mb-1 text-xs font-semibold text-violet-300">Agent</p>}
                  <p className="whitespace-pre-wrap break-words">{message.content}</p>
                  {message.created_at && <time className="mt-2 block text-right text-[11px] text-slate-500">{new Date(message.created_at).toLocaleString()}</time>}
                </article>
              ))}
              {messages.length === 0 && <p className="text-sm text-slate-500">No messages yet.</p>}
            </div>
            <form onSubmit={sendReply} className="border-t border-slate-800 p-3">
              <textarea
                value={reply}
                onChange={(event) => setReply(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    event.currentTarget.form?.requestSubmit();
                  }
                }}
                disabled={!replyEnabled || sending}
                maxLength={4000}
                rows={3}
                placeholder={replyEnabled ? "Write a staff reply... (Enter sends, Shift+Enter adds a line)" : "Replies are available while escalated"}
                className="w-full resize-y rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-violet-500 disabled:opacity-50"
              />
              <div className="mt-2 flex items-center justify-between">
                <span className="text-xs text-slate-500">{reply.length}/4000</span>
                <button disabled={!replyEnabled || !reply.trim() || sending} className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-semibold hover:bg-violet-500 disabled:opacity-50">{sending ? "Sending..." : "Reply"}</button>
              </div>
            </form>
          </section>

          <aside className="space-y-5">
            <section className="space-y-3 rounded-2xl border border-slate-800 bg-slate-900/40 p-4">
              <h2 className="font-semibold">Ticket</h2>
              {ticket ? (
                <>
                  <p className="text-sm">{ticket.title}</p>
                  <p className="text-xs text-slate-400">Priority: {ticket.priority}</p>
                  <div className="flex flex-wrap gap-2">
                    {(["open", "in_progress", "resolved"] as const).map((status) => (
                      <button key={status} disabled={busyTicket} onClick={() => updateTicketStatus(status)} className={`rounded-lg border px-2 py-1 text-xs ${ticket.status === status ? "border-violet-500 text-white" : "border-slate-700 text-slate-400 hover:text-white"} disabled:opacity-50`}>
                        {status.replace("_", " ")}
                      </button>
                    ))}
                  </div>
                </>
              ) : <p className="text-sm text-slate-500">No ticket found for this conversation.</p>}
            </section>

            <section className="space-y-3 rounded-2xl border border-slate-800 bg-slate-900/40 p-4">
              <h2 className="font-semibold">Actions</h2>
              {actions.length === 0 && <p className="text-sm text-slate-500">No proposed actions.</p>}
              {actions.map((action) => {
                const editable = action.status === "pending" || action.status === "failed";
                const draft = drafts[action.id] ?? {};
                const issueUrl = typeof action.result?.url === "string" ? action.result.url : null;
                return (
                  <article key={action.id} className="space-y-3 rounded-xl border border-slate-800 bg-slate-950/70 p-3">
                    <div className="flex items-start justify-between gap-2">
                      <p className="break-all text-sm font-medium">{action.tool === "create_github_issue" ? "Create GitHub issue" : action.tool === "post_slack_alert" ? "Post Slack alert" : action.tool}</p>
                      <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs ${actionBadge[action.status]}`}>{action.status}</span>
                    </div>
                    {action.status === "approved" && (
                      <p role="status" className="text-sm text-blue-300">Queued for execution</p>
                    )}
                    {action.status === "executing" && (
                      <p role="status" className="flex items-center gap-2 text-sm text-violet-300">
                        <span
                          aria-hidden="true"
                          className="h-3 w-3 animate-spin rounded-full border-2 border-violet-300/30 border-t-violet-300"
                        />
                        Executing...
                      </p>
                    )}
                    {editable && action.tool === "create_github_issue" && (
                      <>
                        <label className="block space-y-1 text-xs text-slate-400">Issue title
                          <input value={draft.title ?? ""} onChange={(event) => setDrafts((all) => ({ ...all, [action.id]: { ...draft, title: event.target.value } }))} maxLength={200} className="w-full rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm text-slate-100" />
                        </label>
                        <label className="block space-y-1 text-xs text-slate-400">Issue body
                          <textarea value={draft.body ?? ""} onChange={(event) => setDrafts((all) => ({ ...all, [action.id]: { ...draft, body: event.target.value } }))} maxLength={6000} rows={6} className="w-full rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm text-slate-100" />
                        </label>
                      </>
                    )}
                    {editable && action.tool === "post_slack_alert" && (
                      <label className="block space-y-1 text-xs text-slate-400">Alert text
                        <textarea value={draft.text ?? ""} onChange={(event) => setDrafts((all) => ({ ...all, [action.id]: { ...draft, text: event.target.value } }))} maxLength={2000} rows={5} className="w-full rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm text-slate-100" />
                      </label>
                    )}
                    {!editable && <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words text-xs text-slate-400">{JSON.stringify(action.arguments, null, 2)}</pre>}
                    {issueUrl && <a href={issueUrl} target="_blank" rel="noopener noreferrer" className="block break-all text-sm text-violet-300 underline">Open created issue</a>}
                    {action.status === "failed" && action.error && <p className="whitespace-pre-wrap break-words text-sm text-red-300">{action.error}</p>}
                    {canApprove && editable && (
                      <div className="flex gap-2">
                        <button disabled={busyAction !== null} onClick={() => approve(action)} className="rounded-lg bg-violet-600 px-3 py-1.5 text-sm font-semibold hover:bg-violet-500 disabled:opacity-50">{busyAction === action.id ? "Working..." : action.status === "failed" ? "Retry" : "Approve"}</button>
                        {action.status === "pending" && <button disabled={busyAction !== null} onClick={() => reject(action)} className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800 disabled:opacity-50">Reject</button>}
                      </div>
                    )}
                  </article>
                );
              })}
            </section>
          </aside>
        </div>
      </div>
    </main>
  );
}

export default function ConversationStaffPageWithSuspense() {
  return (
    <Suspense fallback={    <div className="min-h-screen bg-slate-950 p-8 text-slate-300">Loading conversation...</div>}>
      <ConversationStaffPage />
    </Suspense>
  );
}

async function fetchConversation(id: string): Promise<ConversationDetail> {
  return responseJson<ConversationDetail>(await authFetch(`/conversations/${id}`));
}
