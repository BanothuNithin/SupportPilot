"""Support agent graph.

            +--> triage --+
   START ---+             +--> grade --> decide --+--> resolve  --> END   (answer, streamed, with citations)
            +--> retrieve-+                       +--> escalate --> END   (hand over to a human)

triage and retrieve run in parallel. The grader checks that the retrieved context really answers the
question BEFORE any answer is written, so a refusal never carries source chips.
"""
from langgraph.graph import END, START, StateGraph

from app.agents.nodes import decide_node, escalate_node, grade_node, resolve_node, retrieve_node, route, triage_node
from app.agents.state import State


def build_graph():
    g = StateGraph(State)
    g.add_node("triage", triage_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("grade", grade_node)
    g.add_node("decide", decide_node)
    g.add_node("resolve", resolve_node)
    g.add_node("escalate", escalate_node)

    g.add_edge(START, "triage")
    g.add_edge(START, "retrieve")
    g.add_edge(["triage", "retrieve"], "grade")  # waits for BOTH branches
    g.add_edge("grade", "decide")
    g.add_conditional_edges("decide", route, {"resolve": "resolve", "escalate": "escalate"})
    g.add_edge("resolve", END)
    g.add_edge("escalate", END)
    return g.compile()


graph = build_graph()
