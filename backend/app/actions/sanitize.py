import re


def _strip_control(text: str) -> str:
    return "".join(char for char in text if char.isprintable())


def neutralize_github(text: str) -> str:
    text = _strip_control(text)
    return re.sub(r"@(?=\w)", "@\u200b", text)


def neutralize_slack(text: str) -> str:
    text = _strip_control(text)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def truncate(text: str, n: int) -> str:
    if n <= 0:
        return ""
    if len(text) <= n:
        return text
    if n == 1:
        return "…"
    return text[: n - 1] + "…"
