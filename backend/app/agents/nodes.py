import json
import logging
import re
from contextlib import aclosing

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from app.agents import prompts
from app.agents.query import build_search_query
from app.agents.rules import HUMAN_REQUEST, fast_path, rule_triage
from app.agents.state import Grade, State, Triage
from app.core.config import settings
from app.rag import prompt as rag_prompt
from app.rag.retrieve import retrieve

log = logging.getLogger(__name__)

def _parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except ValueError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)  # models sometimes wrap JSON in prose
        if match:
            return json.loads(match.group(0))
        raise


async def triage_node(state: State, config: RunnableConfig) -> dict:
    question = state["question"]
    result: dict[str, object] = {"triage": rule_triage(question)}
    path = fast_path(question)
    if path is not None:
        result["outcome"], result["reason"] = path
    return result


async def retrieve_node(state: State, config: RunnableConfig) -> dict:
    query = build_search_query(state["question"], state.get("history", []))
    return {
        "hits": await retrieve(
            state["tenant_id"],
            query,
            state.get("active_generations", {}),
        ),
        "search_query": query,
    }


async def grade_node(state: State, config: RunnableConfig) -> dict:
    llm = config["configurable"]["llm"]
    hits = state.get("hits", [])
    if not hits:
        return {"grade": Grade(can_answer=False, confidence=0.0)}  # nothing retrieved: no LLM call needed
    context = "\n\n".join(f"[{i}] (source: {h.filename})\n{h.text}" for i, h in enumerate(hits, start=1))
    question = state.get("search_query") or state["question"]
    user = f"QUESTION: {question}\n\nCONTEXT:\n{context}"
    try:
        raw = await llm.complete(
            [{"role": "system", "content": prompts.GRADE}, {"role": "user", "content": user}],
            json_mode=True,
            max_tokens=80,
        )
        grade = Grade.model_validate(_parse_json(raw))
    except Exception:
        log.warning("Grading failed; falling back to retrieval score", exc_info=True)
        return {"grade": Grade(can_answer=True, confidence=hits[0].score)}
    return {"grade": grade}


def decide(triage: Triage, grade: Grade) -> tuple[str, str]:
    """Pure routing rule (easy to unit-test): answer, or hand over to a human, and why."""
    if triage.wants_human:
        return "escalate", "customer_requested_human"
    if triage.intent == "chitchat":
        return "answer", "chitchat"
    if not grade.can_answer:
        return "escalate", "no_reliable_answer"
    if grade.confidence < settings.min_confidence:
        return "escalate", "low_confidence"
    return "answer", "grounded"


async def decide_node(state: State, config: RunnableConfig) -> dict:
    outcome, reason = decide(state["triage"], state["grade"])
    return {"outcome": outcome, "reason": reason}


def route(state: State) -> str:
    return "resolve" if state["outcome"] == "answer" else "escalate"


async def resolve_node(state: State, config: RunnableConfig) -> dict:
    """The Resolver agent: streams an answer from all retrieved context after the grader approves it."""
    llm = config["configurable"]["llm"]
    write = get_stream_writer()
    hits = state.get("hits", [])
    used = hits if state["reason"] == "grounded" else []
    cites = rag_prompt.citations(used)
    if cites:
        write({"type": "citations", "items": cites})
    messages = [{"role": "system", "content": rag_prompt.system_prompt(used)}, *state["history"]]
    cancel = config["configurable"].get("cancel")  # set by the endpoint when the client disconnects
    parts: list[str] = []
    # aclosing() closes the model stream (and its HTTP connection) the moment we stop reading it
    async with aclosing(llm.stream(messages)) as tokens:
        async for token in tokens:
            if cancel is not None and cancel.is_set():
                break  # nobody is listening any more: stop paying for tokens
            parts.append(token)
            write({"type": "token", "text": token})
    return {"answer": "".join(parts), "cites": cites}


async def escalate_node(state: State, config: RunnableConfig) -> dict:
    text = prompts.HANDOFF[state["reason"]]
    get_stream_writer()({"type": "token", "text": text})
    return {"answer": text, "cites": []}
