import uuid
from typing import Literal, TypedDict

from pydantic import BaseModel, field_validator

Outcome = Literal["answer", "escalate"]


class Triage(BaseModel):
    intent: Literal["question", "complaint", "bug_report", "chitchat", "other"] = "question"
    priority: Literal["low", "medium", "high"] = "medium"
    wants_human: bool = False


class Grade(BaseModel):
    """Verdict of the grader: does the retrieved context really answer the question?"""

    can_answer: bool = False
    confidence: float = 0.0

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp(cls, v):
        try:
            return min(1.0, max(0.0, float(v)))
        except (TypeError, ValueError):
            return 0.0


class State(TypedDict, total=False):
    # inputs
    question: str
    history: list[dict]
    tenant_id: object  # uuid.UUID
    active_generations: dict[uuid.UUID, uuid.UUID | None]
    search_query: str
    # produced by nodes
    triage: Triage
    hits: list
    grade: Grade
    outcome: Outcome
    reason: str
    answer: str
    cites: list[dict]
