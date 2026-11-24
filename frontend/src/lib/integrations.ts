import { authFetch } from "./api";

export type IntegrationKind = "github" | "slack";
export type Integration = {
  kind: IntegrationKind;
  configured: boolean;
  summary: string;
};

async function responseError(response: Response): Promise<Error> {
  let message = `Request failed (${response.status})`;
  try {
    const data = await response.json();
    if (typeof data.detail === "string") message = data.detail;
    else if (data.detail) message = JSON.stringify(data.detail);
  } catch {
    // Keep the status message when the response is not JSON.
  }
  return new Error(message);
}

export async function listIntegrations(): Promise<Integration[]> {
  const response = await authFetch("/integrations");
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function saveGithubIntegration(repo: string, token: string): Promise<Integration> {
  const response = await authFetch("/integrations/github", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repo, token }),
  });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function saveSlackIntegration(webhookUrl: string): Promise<Integration> {
  const response = await authFetch("/integrations/slack", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ webhook_url: webhookUrl }),
  });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function removeIntegration(kind: IntegrationKind): Promise<void> {
  const response = await authFetch(`/integrations/${kind}`, { method: "DELETE" });
  if (!response.ok) throw await responseError(response);
}
