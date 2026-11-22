"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { AuthError, getTokens } from "@/lib/api";
import { Ticket, listTickets, setTicketStatus } from "@/lib/tickets";

const priorityStyle: Record<string, string> = {
  high: "bg-red-900/60 text-red-300",
  medium: "bg-amber-900/60 text-amber-300",
  low: "bg-slate-800 text-slate-300",
};

const reasonLabel: Record<string, string> = {
  customer_requested_human: "Customer asked for a human",
  no_reliable_answer: "No reliable answer in the documents",
  low_confidence: "Low confidence",
};

export default function InboxPage() {
  const router = useRouter();
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [error, setError] = useState("");

  const handleError = useCallback(
    (e: unknown) => {
      if (e instanceof AuthError) router.replace("/login");
      else setError(e instanceof Error ? e.message : "Something went wrong.");
    },
    [router],
  );

  useEffect(() => {
    if (!getTokens()) {
      router.replace("/login");
      return;
    }
    (async () => {
      try {
        setTickets(await listTickets());
      } catch (e) {
        handleError(e);
      }
    })();
  }, [router, handleError]);

  async function update(id: string, status: Ticket["status"]) {
    try {
      const updated = await setTicketStatus(id, status);
      setTickets((all) => all.map((t) => (t.id === id ? updated : t)));
    } catch (e) {
      handleError(e);
    }
  }

  return (
    <main className="min-h-screen bg-slate-950 p-6 text-slate-100">
      <div className="mx-auto max-w-3xl space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold">Escalations</h1>
          <Link href="/" className="text-sm text-slate-400 hover:text-white">
            Back to chat
          </Link>
        </div>
        {error && <p className="text-sm text-red-400">{error}</p>}
        <ul className="space-y-2">
          {tickets.length === 0 && <li className="text-slate-400">No escalated conversations.</li>}
          {tickets.map((t) => (
            <li key={t.id} className="rounded-xl border border-slate-800 bg-slate-900/50 px-4 py-3">
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <p className="truncate font-medium">{t.title}</p>
                  <p className="text-xs text-slate-400">{t.reason ? (reasonLabel[t.reason] ?? t.reason) : ""}</p>
                </div>
                <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs ${priorityStyle[t.priority]}`}>{t.priority}</span>
              </div>
              <div className="mt-3 flex items-center gap-2 text-sm">
                <span className="text-slate-400">Status:</span>
                {(["open", "in_progress", "resolved"] as const).map((s) => (
                  <button
                    key={s}
                    onClick={() => update(t.id, s)}
                    className={`rounded-lg border px-2 py-0.5 ${t.status === s ? "border-violet-500 text-white" : "border-slate-700 text-slate-400 hover:text-white"}`}
                  >
                    {s.replace("_", " ")}
                  </button>
                ))}
              </div>
            </li>
          ))}
        </ul>
      </div>
    </main>
  );
}
