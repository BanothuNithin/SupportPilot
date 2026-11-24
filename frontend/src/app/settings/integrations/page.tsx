"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { AuthError, getTokens } from "@/lib/api";
import {
  Integration,
  IntegrationKind,
  listIntegrations,
  removeIntegration,
  saveGithubIntegration,
  saveSlackIntegration,
} from "@/lib/integrations";

const initialIntegrations: Integration[] = [
  { kind: "github", configured: false, summary: "" },
  { kind: "slack", configured: false, summary: "" },
];

export default function IntegrationsPage() {
  const router = useRouter();
  const [integrations, setIntegrations] = useState(initialIntegrations);
  const [repo, setRepo] = useState("");
  const [token, setToken] = useState("");
  const [webhookUrl, setWebhookUrl] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<IntegrationKind | null>(null);

  const handleError = useCallback((reason: unknown) => {
    if (reason instanceof AuthError) router.replace("/login");
    else setError(reason instanceof Error ? reason.message : "Something went wrong.");
  }, [router]);

  const refresh = useCallback(async () => {
    setIntegrations(await listIntegrations());
  }, []);

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

  async function saveGithub(event: React.FormEvent) {
    event.preventDefault();
    setBusy("github");
    setError("");
    try {
      await saveGithubIntegration(repo, token);
      setRepo("");
      setToken("");
      await refresh();
    } catch (reason) {
      handleError(reason);
    } finally {
      setBusy(null);
    }
  }

  async function saveSlack(event: React.FormEvent) {
    event.preventDefault();
    setBusy("slack");
    setError("");
    try {
      await saveSlackIntegration(webhookUrl);
      setWebhookUrl("");
      await refresh();
    } catch (reason) {
      handleError(reason);
    } finally {
      setBusy(null);
    }
  }

  async function remove(kind: IntegrationKind) {
    setBusy(kind);
    setError("");
    try {
      await removeIntegration(kind);
      await refresh();
    } catch (reason) {
      handleError(reason);
    } finally {
      setBusy(null);
    }
  }

  function statusBadge(kind: IntegrationKind) {
    const integration = integrations.find((item) => item.kind === kind);
    if (!integration?.configured) {
      return <span className="rounded-full bg-slate-800 px-2 py-1 text-xs text-slate-300">Not configured</span>;
    }
    return (
      <span className="rounded-full bg-emerald-900/60 px-2 py-1 text-xs text-emerald-300">
        Configured{integration.summary ? ` · ${integration.summary}` : ""}
      </span>
    );
  }

  return (
    <main className="min-h-screen bg-slate-950 p-6 text-slate-100">
      <div className="mx-auto max-w-3xl space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold">Integrations</h1>
          <Link href="/" className="text-sm text-slate-400 hover:text-white">Back to chat</Link>
        </div>
        <p className="text-sm text-amber-300">Use a private repository: issue bodies contain customer text.</p>
        <p className="text-sm text-slate-400">Secrets are encrypted and are not shown again.</p>
        {error && <p role="alert" className="text-sm text-red-400">{error}</p>}

        <section className="space-y-4 rounded-2xl border border-slate-800 bg-slate-900/50 p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-lg font-semibold">GitHub</h2>
            {statusBadge("github")}
          </div>
          <form onSubmit={saveGithub} className="space-y-3">
            <label className="block space-y-1 text-sm text-slate-300">
              Repository (owner/name)
              <input value={repo} onChange={(event) => setRepo(event.target.value)} required placeholder="owner/repository" className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 outline-none focus:border-violet-500" />
            </label>
            <label className="block space-y-1 text-sm text-slate-300">
              Personal access token
              <input type="password" autoComplete="new-password" value={token} onChange={(event) => setToken(event.target.value)} required placeholder="Enter token; it will not be shown again" className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 outline-none focus:border-violet-500" />
            </label>
            <div className="flex gap-2">
              <button disabled={busy !== null} className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-semibold hover:bg-violet-500 disabled:opacity-50">{busy === "github" ? "Saving..." : "Save"}</button>
              <button type="button" disabled={busy !== null || !integrations.find((item) => item.kind === "github")?.configured} onClick={() => remove("github")} className="rounded-lg border border-slate-700 px-4 py-2 text-sm text-slate-300 hover:bg-slate-800 disabled:opacity-50">Remove</button>
            </div>
          </form>
        </section>

        <section className="space-y-4 rounded-2xl border border-slate-800 bg-slate-900/50 p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-lg font-semibold">Slack</h2>
            {statusBadge("slack")}
          </div>
          <form onSubmit={saveSlack} className="space-y-3">
            <label className="block space-y-1 text-sm text-slate-300">
              Incoming webhook URL
              <input type="password" autoComplete="new-password" value={webhookUrl} onChange={(event) => setWebhookUrl(event.target.value)} required placeholder="https://hooks.slack.com/services/..." className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 outline-none focus:border-violet-500" />
            </label>
            <div className="flex gap-2">
              <button disabled={busy !== null} className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-semibold hover:bg-violet-500 disabled:opacity-50">{busy === "slack" ? "Saving..." : "Save"}</button>
              <button type="button" disabled={busy !== null || !integrations.find((item) => item.kind === "slack")?.configured} onClick={() => remove("slack")} className="rounded-lg border border-slate-700 px-4 py-2 text-sm text-slate-300 hover:bg-slate-800 disabled:opacity-50">Remove</button>
            </div>
          </form>
        </section>
      </div>
    </main>
  );
}
