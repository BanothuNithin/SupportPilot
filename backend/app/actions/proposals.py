import re
import uuid

from app.actions.registry import validate_arguments
from app.actions.sanitize import truncate
from app.core.config import settings


def _one_line(text: str) -> str:
    return " ".join(text.replace("\r", " ").replace("\n", " ").split())


def _customer_messages(history: list[dict]) -> list[str]:
    messages = [
        str(item.get("content", ""))
        for item in history
        if item.get("role") == "user"
    ]
    return [truncate(message, 500) for message in messages[-3:]]


def _fence(text: str) -> str:
    longest = max(
        (len(match.group(0)) for match in re.finditer(r"`+", text)),
        default=0,
    )
    return "`" * max(3, longest + 1)


def build_bug_report_actions(
    question: str,
    history: list[dict],
    ticket_id: uuid.UUID,
    conversation_id: uuid.UUID,
    priority: str,
) -> list[tuple[str, dict]]:
    title = "Bug report: " + truncate(_one_line(question), 80)
    customer_text = "\n\n".join(_customer_messages(history) or [_one_line(question)])
    fence = _fence(customer_text)
    body = (
        "Reported via SupportPilot\n"
        f"Priority: {priority}\n"
        f"Ticket ID: {ticket_id}\n"
        f"Conversation ID: {conversation_id}\n\n"
        "Customer-reported text (UNTRUSTED):\n"
        f"{fence}\n{customer_text}\n{fence}\n\n"
        "Reproduction steps: to be filled in by the team."
    )
    github = validate_arguments(
        "create_github_issue",
        {"title": title, "body": body, "labels": ["supportpilot"]},
    )

    slack_title = title
    slack = validate_arguments(
        "post_slack_alert",
        {
            "text": (
                f":bug: New bug report (priority {priority}): {slack_title}\n"
                f"Ticket: {settings.frontend_base_url}/inbox/{conversation_id}"
            )
        },
    )
    return [
        ("create_github_issue", github),
        ("post_slack_alert", slack),
    ]
