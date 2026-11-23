import pytest

from app.agents.rules import fast_path, rule_triage


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Hello", ("answer", "chitchat")),
        ("hi!", ("answer", "chitchat")),
        ("Thank you so much", ("answer", "chitchat")),
        ("who are you?", ("answer", "chitchat")),
        ("What can you do?", ("answer", "chitchat")),
        ("good morning", ("answer", "chitchat")),
        ("I want to talk to a human", ("escalate", "customer_requested_human")),
        ("Hi, what documents are accepted as proof of address?", None),
        ("Thanks, but how long does it take?", None),
        ("What is the capital of France?", None),
    ],
)
def test_fast_path_rules(question, expected):
    assert fast_path(question) == expected


@pytest.mark.parametrize(
    ("question", "intent", "priority"),
    [
        ("my payment failed and I was charged twice", "bug_report", "high"),
        ("the app crashes when I upload a file", "bug_report", "medium"),
        ("this is the worst service, I want a refund", "complaint", "medium"),
        ("how do I update my address", "question", "medium"),
        ("Hello", "chitchat", "low"),
    ],
)
def test_rule_triage(question, intent, priority):
    triage = rule_triage(question)

    assert triage.intent == intent
    assert triage.priority == priority
    assert triage.wants_human is ("human" in question.lower())
