import re

_LEADING_FILLER = re.compile(
    r"^\s*(?:please[\s,]+)?(?:"
    r"write\s+(?:me\s+)?(?:a\s+)?"
    r"(?:(?:very\s+)?(?:long|detailed|short|brief)\s*,?\s*)*"
    r"(?:explanation|summary|overview|description)\s+(?:of|about)\s+(?:the\s+)?"
    r"|explain\s+(?:(?:in\s+detail|in\s+brief|in\s+simple\s+words|briefly)\s+)?"
    r"|tell\s+me\s+about\s+"
    r"|give\s+me\s+(?:a\s+)?"
    r"(?:(?:very\s+)?(?:long|detailed|short|brief)\s*,?\s*)*"
    r"(?:summary|explanation|overview|description)\s+(?:of|about)\s+(?:the\s+)?"
    r")",
    re.IGNORECASE,
)
_TRAILING_FILLER = re.compile(
    r"(?:\s*,?\s*(?:briefly|in\s+brief|in\s+detail|in\s+simple\s+words)\s*[.!?]*)+$",
    re.IGNORECASE,
)
_FOLLOWUP_PREFIX = re.compile(
    r"^(?:continue|go on|carry on|keep going|more|tell me more|elaborate|"
    r"explain that|explain this|explain it|what else|and then|what about|how about)\b",
    re.IGNORECASE,
)
_FOLLOWUP_REFERENCE = re.compile(
    r"\b(?:it|that|this|they|them|those|these|he|she)\b",
    re.IGNORECASE,
)


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def clean_query(text: str) -> str:
    original = text.strip()
    cleaned = _LEADING_FILLER.sub("", original, count=1)
    cleaned = _TRAILING_FILLER.sub("", cleaned).strip()
    return cleaned if _word_count(cleaned) >= 2 else original


def _is_followup(text: str) -> bool:
    return bool(_FOLLOWUP_PREFIX.search(text)) or (
        _word_count(text) <= 6 and bool(_FOLLOWUP_REFERENCE.search(text))
    )


def build_search_query(question: str, history: list[dict[str, str]]) -> str:
    cleaned_current = clean_query(question)
    if not _is_followup(cleaned_current):
        return cleaned_current

    for message in reversed(history[:-1]):
        if message.get("role") != "user":
            continue
        previous = clean_query(message.get("content", ""))
        if not _is_followup(previous):
            return f"{previous} {cleaned_current}".strip()
    return cleaned_current
