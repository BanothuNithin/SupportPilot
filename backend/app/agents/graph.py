"""Support agent graph.

   START --> triage --> fast human request --> escalate --> END
                    |-> fast chitchat ------> resolve  --> END
                    +-> normal question ----> retrieve --> grade --> decide
                                                                  +--> resolve  --> END
                                                                  +--> escalate --> END

Fast paths avoid retrieval and grading. For normal questions, the grader checks that retrieved context
really answers the question BEFORE any answer is written, so a refusal never carries source chips.
"""
from langgraph.graph import END, START, StateGraph

from app.agents.nodes import decide_node, escalate_node, grade_node, resolve_node, retrieve_node, route, triage_node
from app.agents.state import State


def route_after_triage(state: State) -> str:
    if state.get("outcome") == "escalate":
        return "escalate"
    if state.get("outcome") == "answer":
        return "resolve"
    return "retrieve"


def build_graph():
    g = StateGraph(State)
    g.add_node("triage", triage_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("grade", grade_node)
    g.add_node("decide", decide_node)
    g.add_node("resolve", resolve_node)
    g.add_node("escalate", escalate_node)

    g.add_edge(START, "triage")
    g.add_conditional_edges(
        "triage",
        route_after_triage,
        {"escalate": "escalate", "resolve": "resolve", "retrieve": "retrieve"},
    )
    g.add_edge("retrieve", "grade")
    g.add_edge("grade", "decide")
    g.add_conditional_edges("decide", route, {"resolve": "resolve", "escalate": "escalate"})
    g.add_edge("resolve", END)
    g.add_edge("escalate", END)
    return g.compile()


graph = build_graph()
