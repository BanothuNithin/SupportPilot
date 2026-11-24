import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP


HTTP_TIMEOUT_SECONDS = 10.0
mcp = FastMCP("SupportPilot Actions")


class MCPHTTPError(Exception):
    pass


async def create_github_issue_http(
    client: httpx.AsyncClient,
    api_base: str,
    repo: str,
    token: str,
    title: str,
    body: str,
    labels: list[str],
) -> dict:
    url = f"{api_base.rstrip('/')}/repos/{repo}/issues"
    try:
        response = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            json={"title": title, "body": body, "labels": labels},
        )
    except httpx.HTTPError as exc:
        raise MCPHTTPError("GitHub HTTP request failed") from exc
    if response.status_code >= 400:
        message = ""
        try:
            data = response.json()
            if isinstance(data, dict):
                message = str(data.get("message", ""))[:200]
        except Exception:
            pass
        detail = f": {message}" if message else ""
        raise MCPHTTPError(f"GitHub HTTP {response.status_code}{detail}")
    data = response.json()
    return {"number": int(data["number"]), "url": str(data["html_url"])}


async def post_slack_alert_http(
    client: httpx.AsyncClient,
    webhook_url: str,
    text: str,
) -> dict:
    try:
        response = await client.post(webhook_url, json={"text": text})
    except httpx.HTTPError:
        raise MCPHTTPError("Slack HTTP request failed")
    if response.status_code >= 400:
        raise MCPHTTPError(f"Slack HTTP {response.status_code}")
    return {"ok": True}


@mcp.tool()
async def create_github_issue(title: str, body: str, labels: list[str]) -> dict[str, Any]:
    api_base = os.environ.get("GITHUB_API_BASE", "")
    repo = os.environ.get("GITHUB_REPO", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        return await create_github_issue_http(
            client, api_base, repo, token, title, body, labels
        )


@mcp.tool()
async def post_slack_alert(text: str) -> dict[str, Any]:
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        return await post_slack_alert_http(client, webhook_url, text)


if __name__ == "__main__":
    mcp.run(transport="stdio")
