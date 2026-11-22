import { authFetch } from "./api";

export type Doc = {
  id: string;
  filename: string;
  status: "indexing" | "ready" | "failed" | "uploaded";
  chunk_count: number;
  error: string | null;
  created_at: string;
};

async function failure(r: Response): Promise<Error> {
  if (r.status === 403) return new Error("Only owners and admins can manage documents.");
  const body = await r.json().catch(() => null);
  return new Error(body?.detail ?? `Request failed (${r.status})`);
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
