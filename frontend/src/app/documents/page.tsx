"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { AuthError, getTokens } from "@/lib/api";
import {
  Doc,
  deleteDocument,
  downloadDocument,
  listDocuments,
  reindexDocument,
  uploadDocument,
} from "@/lib/documents";

const badge: Record<Doc["status"], string> = {
  queued: "bg-blue-900/60 text-blue-300",
  ready: "bg-emerald-900/60 text-emerald-300",
  indexing: "bg-amber-900/60 text-amber-300",
  failed: "bg-red-900/60 text-red-300",
};

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let size = bytes / 1024;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${size.toFixed(1)} ${units[unitIndex]}`;
}

export default function DocumentsPage() {
  const router = useRouter();
  const [docs, setDocs] = useState<Doc[]>([]);
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [reindexing, setReindexing] = useState<string | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);

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

  const hasQueuedWork = docs.some((d) => d.status === "queued" || d.status === "indexing");
  useEffect(() => {
    if (!hasQueuedWork) return;
    let refreshing = false;
    const timer = setInterval(() => {
      if (refreshing) return;
      refreshing = true;
      void refresh().finally(() => {
        refreshing = false;
      });
    }, 2000);
    return () => clearInterval(timer);
  }, [hasQueuedWork, refresh]);

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

  async function reindex(id: string) {
    if (reindexing !== null) return;
    setReindexing(id);
    setError("");
    try {
      await reindexDocument(id);
      await refresh();
    } catch (err) {
      handleError(err);
    } finally {
      setReindexing(null);
    }
  }

  async function download(doc: Doc) {
    if (downloading !== null) return;
    setDownloading(doc.id);
    setError("");
    try {
      const blob = await downloadDocument(doc.id);
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      try {
        anchor.href = objectUrl;
        anchor.download = doc.filename;
        document.body.appendChild(anchor);
        anchor.click();
      } finally {
        anchor.remove();
        window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
      }
    } catch (err) {
      handleError(err);
    } finally {
      setDownloading(null);
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
                  {d.status === "queued" && "Waiting to be indexed..."}
                  {d.size_bytes != null && ` · ${formatSize(d.size_bytes)}`}
                </p>
              </div>
              <div className="ml-4 flex items-center gap-3">
                <span className={`rounded-full px-2 py-0.5 text-xs ${badge[d.status]}`}>{d.status}</span>
                {(d.status === "failed" || d.status === "ready") && (
                  <button
                    onClick={() => reindex(d.id)}
                    disabled={reindexing !== null}
                    className="text-sm text-violet-300 hover:text-violet-200 disabled:opacity-50"
                  >
                    {reindexing === d.id ? "Reindexing..." : "Reindex"}
                  </button>
                )}
                <button
                  onClick={() => download(d)}
                  disabled={downloading !== null}
                  className="text-sm text-slate-300 hover:text-white disabled:opacity-50"
                >
                  {downloading === d.id ? "Downloading..." : "Download"}
                </button>
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
