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
    r"\b(?:crashes?|fails?|failed|broken|stopped\s+working|"
    r"freezes?|frozen|hangs?|hung|"
    r"stops?\s+responding|unresponsive|"
    r"keeps\s+(?:freezing|crashing|hanging))\b|"
    r"(?:not\s+working|doesn't\s+work)",
    re.IGNORECASE,
)
_WEAK_BUG_WORDS = re.compile(r"\b(?:bugs?|errors?)\b", re.IGNORECASE)
_WEAK_BUG_QUESTION = re.compile(
    r"^(?:what\s+(?:is|does|are)|how\s+(?:do\s+i|to)|where|which|can\s+i|is\s+it)\b",
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
    strong_bug = _BUG_REPORT.search(remaining)
    weak_question = _WEAK_BUG_QUESTION.search(remaining)
    weak_only_bug = bool(_WEAK_BUG_WORDS.search(remaining)) and not strong_bug
    if not (weak_question and weak_only_bug) and strong_bug:
        return "escalate", "bug_report"
    if not (weak_question and weak_only_bug) and re.search(
        r"\b(?:bugs?|errors?)\b", remaining, re.IGNORECASE
    ):
        if strong_bug:
            return "escalate", "bug_report"
    return None


def rule_triage(question: str) -> Triage:
    path = fast_path(question)
    if path == ("escalate", "customer_requested_human"):
        intent = "question"
        wants_human = True
    elif path == ("escalate", "bug_report"):
        intent = "bug_report"
        wants_human = False
    elif path == ("answer", "chitchat"):
        intent = "chitchat"
        wants_human = False
    elif _BUG_REPORT.search(question):
        intent = "bug_report"
        wants_human = bool(HUMAN_REQUEST.search(question))
    elif _COMPLAINT.search(question):
        intent = "complaint"
        wants_human = bool(HUMAN_REQUEST.search(question))
    else:
        intent = "question"
        wants_human = bool(HUMAN_REQUEST.search(question))

    if _HIGH_PRIORITY.search(question):
        priority = "high"
    elif intent == "chitchat":
        priority = "low"
    else:
        priority = "medium"

    return Triage(
        intent=intent,
        priority=priority,
        wants_human=wants_human,
    )
