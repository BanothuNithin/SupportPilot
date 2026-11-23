import re

from app.agents.state import Triage

HUMAN_REQUEST = re.compile(
    r"\b(human|real person|representative|live agent|support agent|"
    r"customer (care|service) (agent|executive|team)|"
    r"(talk|speak|chat) (to|with) (a |an |the )?(person|someone|somebody|agent|manager|support))\b",
    re.IGNORECASE,
)

_SOCIAL_PREFIX = re.compile(
    r"^(?:(?:good morning|good afternoon|good evening|thank you(?: so much| very much)?|"
    r"thanks(?: so much| a lot)?|goodbye|hello|hey|hi|thx|okay|ok|bye)"
    r"\b[\s,!.?;:-]*)+",
    re.IGNORECASE,
)
_META_MESSAGE = re.compile(
    r"^(?:who are you|what are you|what is your name|what can you do|how are you|"
    r"can you help me|could you help(?: me)?)\s*[?.!]*$",
    re.IGNORECASE,
)
_BUG_REPORT = re.compile(
    r"\b(?:bugs?|crashes?|errors?|not\s+working|broken|fails?|failed)\b",
    re.IGNORECASE,
)
_COMPLAINT = re.compile(
    r"\b(?:worst|terrible|unacceptable|complaints?|refunds?)\b",
    re.IGNORECASE,
)
_HIGH_PRIORITY = re.compile(
    r"\b(?:urgent|asap|outages?|is\s+down|can't\s+log\s+in|cannot\s+log\s+in|"
    r"data\s+loss|hacked|security|charged\s+twice|payment\s+failed|fraud)\b",
    re.IGNORECASE,
)


def fast_path(question: str) -> tuple[str, str] | None:
    if HUMAN_REQUEST.search(question):
        return "escalate", "customer_requested_human"

    remaining = _SOCIAL_PREFIX.sub("", question.strip(), count=1).strip()
    if not remaining or _META_MESSAGE.fullmatch(remaining):
        return "answer", "chitchat"
    return None


def rule_triage(question: str) -> Triage:
    path = fast_path(question)
    if path == ("answer", "chitchat"):
        intent = "chitchat"
    elif _BUG_REPORT.search(question):
        intent = "bug_report"
    elif _COMPLAINT.search(question):
        intent = "complaint"
    else:
        intent = "question"

    if _HIGH_PRIORITY.search(question):
        priority = "high"
    elif intent == "chitchat":
        priority = "low"
    else:
        priority = "medium"

    return Triage(
        intent=intent,
        priority=priority,
        wants_human=bool(HUMAN_REQUEST.search(question)),
    )
