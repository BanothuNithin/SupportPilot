import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from app.actions.executor import McpToolExecutor, redact
from app.actions.registry import (
    UnknownTool,
    validate_arguments,
)
from app.actions.sanitize import (
    neutralize_github,
    neutralize_slack,
    truncate,
)
from app.mcp_server.server import create_github_issue_http, post_slack_alert_http


def test_sanitize_github_mentions_controls_and_truncate():
    assert neutralize_github("@everyone hi @bob") == "@\u200beveryone hi @\u200bbob"
    assert "\u0000" not in neutralize_github("hello\u0000world")
    assert truncate("abcdefgh", 5) == "abcd…"


def test_sanitize_slack():
    text = "<!channel> <@U123> <#C123> <http://evil|click> &"
    sanitized = neutralize_slack(text)
    assert "&lt;!channel&gt;" in sanitized
    assert "&lt;@U123&gt;" in sanitized
    assert "&lt;#C123&gt;" in sanitized
    assert "&lt;http://evil|click&gt;" in sanitized
    assert "&amp;" in sanitized


@pytest.mark.parametrize(
    "tool,raw",
    [
        ("unknown", {}),
        ("create_github_issue", {"title": "x" * 201, "body": "body"}),
        ("create_github_issue", {"title": "title", "body": "body", "extra": True}),
        ("create_github_issue", {"title": "title"}),
        ("post_slack_alert", {"text": ""}),
        ("post_slack_alert", {"text": "hello", "extra": True}),
    ],
)
def test_registry_rejects_invalid_arguments(tool, raw):
    if tool == "unknown":
        with pytest.raises(UnknownTool):
            validate_arguments(tool, raw)
    else:
        with pytest.raises(Exception):
            validate_arguments(tool, raw)


def test_registry_labels_capped_and_sanitizes():
    with pytest.raises(Exception):
        validate_arguments(
            "create_github_issue",
            {"title": "title", "body": "body", "labels": ["x"] * 6},
        )
    result = validate_arguments(
        "create_github_issue",
        {"title": "@bob", "body": "@everyone\nok\u0000", "labels": ["bug"]},
    )
    assert "@\u200bbob" in result["title"]
    assert "@\u200beveryone" in result["body"]
    assert "\u0000" not in result["body"]


@pytest.mark.asyncio
async def test_github_http_success_url_headers_and_body():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["Authorization"]
        seen["accept"] = request.headers["Accept"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"number": 42, "html_url": "https://github.test/i/42"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await create_github_issue_http(
            client,
            "https://github.test/api",
            "owner/repo",
            "secret-token",
            "@\u200bbob",
            "safe body",
            ["bug"],
        )
    assert seen["url"] == "https://github.test/api/repos/owner/repo/issues"
    assert seen["authorization"] == "Bearer secret-token"
    assert seen["accept"] == "application/vnd.github+json"
    assert seen["body"] == {"title": "@\u200bbob", "body": "safe body", "labels": ["bug"]}
    assert result == {"number": 42, "url": "https://github.test/i/42"}


@pytest.mark.parametrize("status", [401, 404, 500])
@pytest.mark.asyncio
async def test_github_http_errors_do_not_leak_token(status):
    token = "ghp_verysecret123456"

    def handler(request):
        return httpx.Response(status, json={"message": "bad credentials " + "x" * 300})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(Exception) as exc:
            await create_github_issue_http(
                client,
                "https://github.test/api",
                "owner/repo",
                token,
                "title",
                "body",
                [],
            )
    message = str(exc.value)
    assert str(status) in message
    assert token not in message
    assert len(message) < 400


@pytest.mark.asyncio
async def test_slack_http_success():
    seen = {}

    def success(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    webhook = "https://hooks.slack.com/services/secretpath"
    async with httpx.AsyncClient(transport=httpx.MockTransport(success)) as client:
        result = await post_slack_alert_http(client, webhook, "<b>")
    assert seen == {"url": webhook, "body": {"text": "<b>"}}
    assert result == {"ok": True}


@pytest.mark.parametrize("status", [401, 404, 500])
@pytest.mark.asyncio
async def test_slack_http_errors_do_not_leak_webhook(status):
    webhook = "https://hooks.slack.com/services/verysecret"

    def failure(request):
        return httpx.Response(status, text="internal " + webhook)

    async with httpx.AsyncClient(transport=httpx.MockTransport(failure)) as client:
        with pytest.raises(Exception) as exc:
            await post_slack_alert_http(client, webhook, "text")
    assert str(status) in str(exc.value)
    assert webhook not in str(exc.value)


def test_redact():
    env = {"TOKEN": "ghp_abcdefgh123456", "SHORT": "tiny"}
    assert redact("Bearer ghp_abcdefgh123456 tiny", env) == "Bearer [redacted] tiny"


@pytest.mark.mcp_integration
@pytest.mark.asyncio
async def test_real_stdio_github_executor(tmp_path):
    received = {}
    token = "ghp_REALTESTTOKEN123"

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            received["path"] = self.path
            received["authorization"] = self.headers.get("Authorization")
            received["body"] = json.loads(self.rfile.read(length))
            payload = json.dumps({"number": 73, "html_url": "http://127.0.0.1/fake/issues/73"}).encode()
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    tool_arguments = {
        "title": "@everyone",
        "body": "@bob hello",
        "labels": ["bug"],
    }
    try:
        executor = McpToolExecutor()
        result = await executor.call(
            "create_github_issue",
            tool_arguments,
            {
                "GITHUB_API_BASE": f"http://127.0.0.1:{port}",
                "GITHUB_REPO": "owner/repo",
                "GITHUB_TOKEN": token,
            },
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert received["path"] == "/repos/owner/repo/issues"
    assert received["authorization"] == f"Bearer {token}"
    assert "@\u200beveryone" in received["body"]["title"]
    assert "@\u200bbob" in received["body"]["body"]
    assert token not in json.dumps(tool_arguments)
    assert token not in json.dumps(received["body"])
    assert result["number"] == 73
    assert result["url"] == "http://127.0.0.1/fake/issues/73"
