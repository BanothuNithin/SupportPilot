"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  AuthError,
  Conversation,
  Message,
  createConversation,
  getMessages,
  getTokens,
  listConversations,
  logout,
  streamMessage,
} from "@/lib/api";

export default function ChatPage() {
  const router = useRouter();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortControllerRef.current?.abort(), []);

  // Any API call can fail with AuthError; send the user to the login page.
  const handleError = useCallback(
    (e: unknown) => {
      if (e instanceof AuthError) router.replace("/login");
      else setError(e instanceof Error ? e.message : "Something went wrong.");
    },
    [router],
  );

  const openConversation = useCallback(
    async (id: string) => {
      setActiveId(id);
      setError("");
      try {
        setMessages(await getMessages(id));
      } catch (e) {
        handleError(e);
      }
    },
    [handleError],
  );

  useEffect(() => {
    if (!getTokens()) {
      router.replace("/login");
      return;
    }
    (async () => {
      try {
        const list = await listConversations();
        setConversations(list);
        if (list[0]) await openConversation(list[0].id);
      } catch (e) {
        handleError(e);
      }
    })();
  }, [router, handleError, openConversation]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function newChat() {
    try {
      const conv = await createConversation();
      setConversations((c) => [conv, ...c]);
      setActiveId(conv.id);
      setMessages([]);
      setError("");
    } catch (e) {
      handleError(e);
    }
  }

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || !activeId || streaming || abortControllerRef.current) return;
    setInput("");
    setError("");
    setStreaming(true);
    const controller = new AbortController();
    const partialMessageId = `partial-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    abortControllerRef.current = controller;
    // show the customer message and an empty AI bubble immediately, then fill the bubble token by token
    setMessages((m) => [...m, { id: `u-${Date.now()}`, sender: "customer", content: text }, { id: "streaming", sender: "ai", content: "" }]);
    let aborted = false;
    try {
      await streamMessage(activeId, text, (token) =>
        setMessages((m) => m.map((x) => (x.id === "streaming" ? { ...x, content: x.content + token } : x))),
        controller.signal,
        (citations) =>
          setMessages((m) => m.map((x) => (x.id === "streaming" ? { ...x, citations } : x))),
      );
    } catch (err) {
      aborted = controller.signal.aborted || (err instanceof Error && err.name === "AbortError");
      if (aborted) {
        setMessages((current) =>
          current.flatMap((message) => {
            if (message.id !== "streaming") return [message];
            return message.content ? [{ ...message, id: partialMessageId }] : [];
          }),
        );
      } else {
        handleError(err);
      }
    } finally {
      if (abortControllerRef.current === controller) abortControllerRef.current = null;
      setStreaming(false);
      if (!aborted && activeId) setMessages(await getMessages(activeId).catch(() => []));
    }
  }

  async function signOut() {
    await logout();
    router.replace("/login");
  }

  return (
    <div className="flex h-screen bg-slate-950 text-slate-100">
      <aside className="flex w-64 flex-col border-r border-slate-800 bg-slate-900/50">
        <div className="p-4 text-lg font-bold">SupportPilot</div>
        <button onClick={newChat} className="mx-3 mb-3 rounded-lg bg-violet-600 py-2 text-sm font-semibold hover:bg-violet-500">
          + New chat
        </button>
        <a href="/documents" className="mx-3 mb-3 rounded-lg px-3 py-2 text-sm text-slate-300 hover:bg-slate-800">
          Knowledge base
        </a>
        <div className="flex-1 space-y-1 overflow-y-auto px-3">
          {conversations.map((c) => (
            <button
              key={c.id}
              onClick={() => openConversation(c.id)}
              className={`w-full truncate rounded-lg px-3 py-2 text-left text-sm ${c.id === activeId ? "bg-slate-800" : "hover:bg-slate-800/60"}`}
            >
              Chat {new Date(c.created_at).toLocaleString()}
            </button>
          ))}
        </div>
        <button onClick={signOut} className="m-3 rounded-lg border border-slate-700 py-2 text-sm text-slate-300 hover:bg-slate-800">
          Sign out
        </button>
      </aside>

      <main className="flex flex-1 flex-col">
        <div className="flex-1 space-y-4 overflow-y-auto p-6">
          {!activeId && <p className="text-slate-400">Create a new chat to start.</p>}
          {messages.map((m) => {
            const referencedNumbers = new Set(
              Array.from(m.content.matchAll(/\[(\d+)\]/g), ([, number]) => Number(number)),
            );
            const citations = m.citations?.filter((citation) => referencedNumbers.has(citation.n)) ?? [];

            return (
              <div key={m.id} className={`flex ${m.sender === "customer" ? "justify-end" : "justify-start"}`}>
                <div className={m.sender === "customer" ? "max-w-xl" : "flex max-w-xl flex-col items-start"}>
                  <div
                    className={`whitespace-pre-wrap rounded-2xl px-4 py-2 ${
                      m.sender === "customer" ? "bg-violet-600 text-white" : "bg-slate-800 text-slate-100"
                    }`}
                  >
                    {m.content || (streaming && m.id === "streaming" ? "..." : "")}
                  </div>
                  {m.sender === "ai" && citations.length > 0 ? (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {citations.map((citation) => (
                        <span
                          key={`${citation.document_id}-${citation.chunk_index}-${citation.n}`}
                          title={citation.snippet}
                          className="rounded-full border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-300"
                        >
                          [{citation.n}] {citation.filename}, section {citation.chunk_index + 1}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </div>
              </div>
            );
          })}
          <div ref={bottomRef} />
        </div>
        {error && <p className="px-6 pb-2 text-sm text-red-400">{error}</p>}
        <form onSubmit={send} className="flex gap-2 border-t border-slate-800 p-4">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={!activeId || streaming}
            placeholder={activeId ? "Type a message..." : "Create a chat first"}
            className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 outline-none focus:border-violet-500 disabled:opacity-50"
          />
          {streaming ? (
            <button
              type="button"
              onClick={() => abortControllerRef.current?.abort()}
              className="rounded-lg bg-red-600 px-5 font-semibold hover:bg-red-500"
            >
              Stop
            </button>
          ) : (
            <button disabled={!activeId || !input.trim()} className="rounded-lg bg-violet-600 px-5 font-semibold hover:bg-violet-500 disabled:opacity-50">
              Send
            </button>
          )}
        </form>
      </main>
    </div>
  );
}
