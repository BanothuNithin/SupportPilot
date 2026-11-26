import { authFetch } from "./api";

export type Doc = {
  id: string;
  filename: string;
  status: "queued" | "indexing" | "ready" | "failed";
  chunk_count: number;
  error: string | null;
  created_at: string;
  size_bytes?: number | null;
};

async function failure(r: Response): Promise<Error> {
  if (r.status === 403) return new Error("Only owners and admins can manage documents.");
  const body = await r.json().catch(() => null);
  return new Error(
    typeof body?.detail === "string" ? body.detail : `Request failed (${r.status})`,
  );
}

export async function listDocuments(): Promise<Doc[]> {
  const r = await authFetch("/documents");
  if (!r.ok) throw await failure(r);
  return r.json();
}

export async function uploadDocument(file: File): Promise<Doc> {
  const form = new FormData();
  form.append("file", file); // do not set Content-Type: the browser adds the multipart boundary
  const r = await authFetch("/documents", { method: "POST", body: form });
  if (!r.ok) throw await failure(r);
  return r.json();
}

export async function deleteDocument(id: string): Promise<void> {
  const r = await authFetch(`/documents/${id}`, { method: "DELETE" });
  if (!r.ok) throw await failure(r);
}

export async function reindexDocument(id: string): Promise<Doc> {
  const r = await authFetch(`/documents/${id}/reindex`, { method: "POST" });
  if (!r.ok) throw await failure(r);
  return r.json();
}

export async function downloadDocument(id: string): Promise<Blob> {
  const r = await authFetch(`/documents/${id}/download`);
  if (!r.ok) throw await failure(r);
  return r.blob();
}
