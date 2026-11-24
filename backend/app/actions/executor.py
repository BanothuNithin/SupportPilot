import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Protocol

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.actions.registry import validate_arguments
from app.core.config import settings


class ToolExecutionError(Exception):
    pass


class ToolExecutor(Protocol):
    async def call(self, tool: str, arguments: dict, env: dict[str, str]) -> dict: ...


def redact(message: str, env: dict[str, str]) -> str:
    result = message
    for value in sorted(
        {value for value in env.values() if isinstance(value, str) and len(value) >= 8},
        key=len,
        reverse=True,
    ):
        result = result.replace(value, "[redacted]")
    return result


class McpToolExecutor:
    async def call(self, tool: str, arguments: dict, env: dict[str, str]) -> dict:
        validated = validate_arguments(tool, arguments)
        backend_dir = Path(__file__).resolve().parents[2]
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp_server.server"],
            cwd=backend_dir,
            env=dict(env),
        )

        async def execute() -> dict:
            async with stdio_client(parameters) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        tool,
                        validated,
                        read_timeout_seconds=timedelta(
                            seconds=settings.mcp_timeout_seconds
                        ),
                    )
                    if getattr(result, "isError", False):
                        raise ToolExecutionError(
                            redact(
                                "\n".join(
                                    getattr(item, "text", "")
                                    for item in getattr(result, "content", [])
                                ),
                                env,
                            )
                        )
                    structured = getattr(result, "structuredContent", None)
                    if structured is not None:
                        return structured
                    content = getattr(result, "content", [])
                    if content:
                        text = getattr(content[0], "text", "")
                        try:
                            import json

                            return json.loads(text)
                        except Exception as exc:
                            raise ToolExecutionError(redact(str(exc), env)) from exc
                    raise ToolExecutionError(redact("MCP tool returned no result", env))

        try:
            with anyio.fail_after(settings.mcp_timeout_seconds):
                return await execute()
        except TimeoutError as exc:
            raise ToolExecutionError(redact("MCP tool call timed out", env)) from exc
        except ToolExecutionError:
            raise
        except Exception as exc:
            raise ToolExecutionError(redact(str(exc), env)) from exc


def get_tool_executor() -> ToolExecutor:
    return McpToolExecutor()
