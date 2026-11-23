import pytest

from app.agents.query import build_search_query, clean_query


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Write a very long, detailed explanation of the KYC rules for proof of address",
            "KYC rules for proof of address",
        ),
        ("Explain in detail how to open an account", "how to open an account"),
        ("Tell me about video KYC", "video KYC"),
        ("Give me a short summary of the small account rules", "small account rules"),
        ("Please explain the OVD rules briefly", "the OVD rules"),
        (
            "What documents are accepted as proof of address?",
            "What documents are accepted as proof of address?",
        ),
        ("How do I update my address?", "How do I update my address?"),
        ("  Please explain KYC  ", "Please explain KYC"),
    ],
)
def test_clean_query(text, expected):
    assert clean_query(text) == expected


def test_build_search_query_expands_followup_with_previous_user_question():
    history = [
        {"role": "user", "content": "What documents are accepted as proof of address?"},
        {"role": "assistant", "content": "Utility bills and bank statements."},
        {"role": "user", "content": "continue"},
    ]

    query = build_search_query("continue", history)

    assert "documents" in query
    assert "proof of address" in query
    assert query.endswith("continue")


def test_build_search_query_skips_earlier_followup_to_find_real_question():
    history = [
        {"role": "user", "content": "How does video KYC work?"},
        {"role": "assistant", "content": "It verifies your identity remotely."},
        {"role": "user", "content": "continue"},
        {"role": "assistant", "content": "The next step is..."},
        {"role": "user", "content": "continue"},
    ]

    query = build_search_query("continue", history)

    assert "video KYC" in query
    assert query.endswith("continue")


def test_build_search_query_leaves_first_followup_unexpanded():
    assert build_search_query("continue", [{"role": "user", "content": "continue"}]) == "continue"


def test_build_search_query_does_not_expand_a_normal_long_question():
    question = "What documents are accepted as proof of address?"
    history = [
        {"role": "user", "content": "How do I reset my password?"},
        {"role": "assistant", "content": "Use the reset link."},
        {"role": "user", "content": question},
    ]

    assert build_search_query(question, history) == question


@pytest.mark.parametrize(
    "question",
    [
        "What is V-CIP?",
        "Define OVD",
        "Why is KYC mandatory?",
        "How do I update my address?",
    ],
)
def test_standalone_questions_are_not_expanded(question):
    history = [
        {"role": "user", "content": "What documents are accepted as proof of address?"},
        {"role": "assistant", "content": "Utility bills are accepted."},
        {"role": "user", "content": question},
    ]

    assert build_search_query(question, history) == clean_query(question)


@pytest.mark.parametrize(
    "question",
    [
        "continue your answer",
        "tell me more",
        "what about NRIs?",
        "Is it mandatory?",
    ],
)
def test_followup_phrases_and_references_expand_from_previous_question(question):
    history = [
        {"role": "user", "content": "What documents are accepted as proof of address?"},
        {"role": "assistant", "content": "Utility bills are accepted."},
        {"role": "user", "content": question},
    ]

    query = build_search_query(question, history)

    assert "proof of address" in query
    assert query.endswith(clean_query(question))
