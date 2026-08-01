"""Multi-hop question answering over a pack's graph and corpus.

Hybrid search answers from text that resembles the question. Some questions have
no such text: "what breaks if urllib3 breaks" is two hops of an edge type, and
no passage in the corpus states the answer. This finds the entity first, walks
the graph, and reads afterwards.

Every run leaves a trace — which steps ran, which nodes and edges each touched —
so a wrong answer can be attributed to a step and a replay can be drawn from it.
"""

from graphpack.agent.contract import Intent, RetrievalError, RetrievalRules, load_retrieval_rules
from graphpack.agent.loop import answer_question
from graphpack.agent.trace import STEPS, Trace, TraceEvent

__all__ = [
    "STEPS",
    "Intent",
    "RetrievalError",
    "RetrievalRules",
    "Trace",
    "TraceEvent",
    "answer_question",
    "load_retrieval_rules",
]
