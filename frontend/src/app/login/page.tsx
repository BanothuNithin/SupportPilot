"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { login, register } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [company, setCompany] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (mode === "login") await login(email, password);
      else await register(company, email, password);
      router.push("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  const input = "w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100 outline-none focus:border-violet-500";

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-950 p-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
        <h1 className="text-2xl font-bold text-white">SupportPilot</h1>
        <p className="text-sm text-slate-400">{mode === "login" ? "Sign in to your workspace" : "Create a workspace for your company"}</p>
        {mode === "register" && (
          <input className={input} placeholder="Company name" value={company} onChange={(e) => setCompany(e.target.value)} required minLength={2} />
        )}
        <input className={input} type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        <input className={input} type="password" placeholder="Password (8+ characters)" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8} />
        {error && <p className="text-sm text-red-400">{error}</p>}
        <button disabled={busy} className="w-full rounded-lg bg-violet-600 py-2 font-semibold text-white hover:bg-violet-500 disabled:opacity-50">
          {busy ? "Please wait..." : mode === "login" ? "Sign in" : "Create workspace"}
        </button>
        <button type="button" onClick={() => setMode(mode === "login" ? "register" : "login")} className="w-full text-sm text-slate-400 hover:text-white">
          {mode === "login" ? "New here? Create a workspace" : "Already have an account? Sign in"}
        </button>
      </form>
    </main>
  );
}
