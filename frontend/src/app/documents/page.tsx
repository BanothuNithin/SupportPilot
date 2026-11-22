"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { AuthError, getTokens } from "@/lib/api";
import { Doc, deleteDocument, listDocuments, uploadDocument } from "@/lib/documents";

const badge: Record<string, string> = {
  ready: "bg-emerald-900/60 text-emerald-300",
  indexing: "bg-amber-900/60 text-amber-300",
  failed: "bg-red-900/60 text-red-300",
  uploaded: "bg-slate-800 text-slate-300",
};

export default function DocumentsPage() {
  const router = useRouter();
  const [docs, setDocs] = useState<Doc[]>([]);
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);

  const handleError = useCallback(
    (e: unknown) => {
      if (e instanceof AuthError) router.replace("/login");
      else setError(e instanceof Error ? e.message : "Something went wrong.");
    },
    [router],
  );

  const refresh = useCallback(async () => {
    try {
      setDocs(await listDocuments());
    } catch (e) {
      handleError(e);
    }
  }, [handleError]);

  useEffect(() => {
    if (!getTokens()) {
      router.replace("/login");
      return;
    }
    (async () => {
      try {
        setDocs(await listDocuments());
      } catch (e) {
        handleError(e);
      }
    })();
  }, [router, handleError]);

  // while any document is still indexing, poll every 2 seconds
  const anyIndexing = docs.some((d) => d.status === "indexing");
  useEffect(() => {
    if (!anyIndexing) return;
    const timer = setInterval(refresh, 2000);
    return () => clearInterval(timer);
  }, [anyIndexing, refresh]);

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setError("");
    setUploading(true);
    try {
      await uploadDocument(file);
      await refresh();
    } catch (err) {
      handleError(err);
    } finally {
      setUploading(false);
    }
  }

  async function remove(id: string) {
    if (!confirm("Delete this document and its knowledge?")) return;
    try {
      await deleteDocument(id);
      await refresh();
    } catch (err) {
      handleError(err);
    }
  }

  return (
    <main className="min-h-screen bg-slate-950 p-6 text-slate-100">
      <div className="mx-auto max-w-3xl space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold">Knowledge base</h1>
          <Link href="/" className="text-sm text-slate-400 hover:text-white">
            Back to chat
          </Link>
        </div>

        <label className="block cursor-pointer rounded-2xl border border-dashed border-slate-700 bg-slate-900/50 p-8 text-center hover:border-violet-500">
          <input type="file" accept=".txt,.md,.pdf" onChange={onFile} disabled={uploading} className="hidden" />
          <span className="font-semibold">{uploading ? "Uploading..." : "Click to upload a document"}</span>
          <span className="mt-1 block text-sm text-slate-400">.txt, .md or .pdf, up to 5 MB</span>
        </label>

        {error && <p className="text-sm text-red-400">{error}</p>}

        <ul className="space-y-2">
          {docs.length === 0 && <li className="text-slate-400">No documents yet. Upload one so the AI can answer from it.</li>}
          {docs.map((d) => (
            <li key={d.id} className="flex items-center justify-between rounded-xl border border-slate-800 bg-slate-900/50 px-4 py-3">
              <div className="min-w-0">
                <p className="truncate font-medium">{d.filename}</p>
                <p className="text-xs text-slate-400">
                  {d.status === "ready" && `${d.chunk_count} chunks`}
                  {d.status === "failed" && (d.error ?? "Indexing failed")}
                  {d.status === "indexing" && "Reading, chunking and embedding..."}
                </p>
              </div>
              <div className="ml-4 flex items-center gap-3">
                <span className={`rounded-full px-2 py-0.5 text-xs ${badge[d.status] ?? badge.uploaded}`}>{d.status}</span>
                <button onClick={() => remove(d.id)} className="text-sm text-slate-400 hover:text-red-400">
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </main>
  );
}
