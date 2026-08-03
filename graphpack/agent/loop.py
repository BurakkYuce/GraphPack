"""The loop: route, look up, walk, read, answer, check.

Written out rather than built on a graph framework. The plan named LangGraph and
this is a deliberate departure: the machine has five steps and one conditional
edge, the trace schema is a contract with the visualisation that wants exact
control, and the alternative adds fourteen packages including langchain-core to
an environment shared with an engine whose own pyproject carries three separate
warnings about langchain version conflicts. None of what the framework offers —
checkpointing, parallel branches, a DSL for five nodes — is needed here, and the
loop is easier to test as plain code.

What makes this more than a retriever is the second pass. Vanilla hybrid search
answers from whatever text resembles the question. This finds the entity the
question is about, walks the graph from it, and only then reads — so a question
whose answer is two edges away can be answered even when no passage states it.
"""

from __future__ import annotations

import logging
import re

from graphpack.agent.contract import Intent, RetrievalRules
from graphpack.agent.tools import (
    Gathered,
    lookup,
    resolve_mention,
    search,
    traverse,
    verify,
)
from graphpack.agent.trace import Recorder, Trace

logger = logging.getLogger(__name__)

#: Words too common to be the subject of a question. Kept short on purpose: the
#: entity lookup is a graph query, so a wrong candidate costs a miss rather than
#: a wrong answer, and being aggressive here loses real entity names.
_NOISE = frozenset(
    {
        "what",
        "which",
        "when",
        "does",
        "would",
        "break",
        "breaks",
        "happen",
        "happens",
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "about",
        "hangi",
        "nedir",
        "kimdir",
        "olur",
        "kararlar",
        "karar",
        "madde",
        "sayılı",
    }
)


def answer_question(
    session,
    pack: str,
    question: str,
    rules: RetrievalRules,
    system=None,
    llm=None,
    resolver=None,
) -> Trace:
    """Answer one question, recording every step.

    ``system`` supplies hybrid search and ``llm`` writes the reply; both are
    optional. Without them the run still traverses the graph and returns what it
    found, which is how the deterministic half is tested without a model.

    ``resolver`` is the pack's resolution rules with the backbone indexed. When
    given, a question naming an entity the way prose names it — "Sendikalar
    Kanunu" rather than 6356 — reaches the right node.
    """
    recorder = Recorder(question=question, pack=pack)
    gathered = Gathered()

    intent = _route(recorder, question, rules, llm)
    _lookup(recorder, session, pack, question, rules, gathered, resolver)
    if intent is not None:
        _expand(recorder, session, pack, intent, gathered)
    if system is not None:
        _retrieve(recorder, system, question, gathered)

    _answer(recorder, question, gathered, llm, intent)
    _critique(recorder, session, pack, gathered)
    return recorder.trace


# ----------------------------------------------------------------------
# Steps
# ----------------------------------------------------------------------


def _route(recorder: Recorder, question: str, rules: RetrievalRules, llm) -> Intent | None:
    """Decide what kind of question this is.

    Keyword matching first because it is free and deterministic. The model is
    asked only when the pack's phrases do not settle it, which keeps the
    least predictable part of the loop off the common path.
    """
    with recorder.step("route", tool="keywords") as event:
        intent, score = rules.route(question)
        if intent is not None:
            event.summary = f"{intent.name} (matched {score} phrase(s))"
            event.detail = {"intent": intent.name, "score": score, "how": "keywords"}
            return intent

        if llm is None:
            event.summary = "no intent matched, and no model to ask"
            return None

        event.tool = "llm"
        chosen = _ask_model_to_route(llm, question, rules)
        event.summary = f"{chosen.name} (chosen by model)" if chosen else "no intent applies"
        event.detail = {"intent": chosen.name if chosen else None, "how": "llm"}
        return chosen


def _lookup(
    recorder: Recorder,
    session,
    pack: str,
    question: str,
    rules: RetrievalRules,
    gathered: Gathered,
    resolver=None,
) -> None:
    """Find which graph entities the question is about.

    Two routes, in order of precision. The pack's lookup Cypher matches an
    identifier or a name outright. The pack's resolution rules handle everything
    else — abbreviations, inflections, titles instead of numbers — and they
    already exist, so the agent uses them rather than growing its own copy.
    """
    with recorder.step("lookup", tool="cypher") as event:
        for candidate in _candidates(question):
            reached = lookup(session, pack, candidate, rules) or resolve_mention(
                candidate, resolver
            )
            for found in reached:
                if found.id not in {e.id for e in gathered.entities}:
                    gathered.entities.append(found)
        if any(e.via not in ("lookup", "") for e in gathered.entities):
            event.tool = "cypher+resolve"

        event.node_ids = [e.id for e in gathered.entities]
        event.summary = (
            f"{len(gathered.entities)} entity(ies): {', '.join(event.node_ids[:5])}"
            if gathered.entities
            else "the question names nothing the graph holds"
        )


def _expand(recorder: Recorder, session, pack: str, intent: Intent, gathered: Gathered) -> None:
    """Walk the graph from each entity found."""
    with recorder.step("expand", tool=intent.name) as event:
        for entity in gathered.entities:
            found, edges = traverse(session, pack, intent, entity.id)
            known = {n.id for n in gathered.neighbours} | {e.id for e in gathered.entities}
            gathered.neighbours.extend(n for n in found if n.id not in known)
            gathered.edges.extend(edges)

        event.node_ids = [n.id for n in gathered.neighbours]
        event.edge_ids = gathered.edges
        event.summary = (
            f"{len(gathered.neighbours)} reached over {intent.hops} hop(s)"
            if gathered.neighbours
            else "nothing reachable"
        )


def _retrieve(recorder: Recorder, system, question: str, gathered: Gathered) -> None:
    """Read the corpus for text that supports an answer."""
    with recorder.step("retrieve", tool="hybrid") as event:
        gathered.passages = search(system, question)
        event.summary = f"{len(gathered.passages)} passage(s)"
        event.detail = {"documents": [p.document for p in gathered.passages][:5]}


def _answer(
    recorder: Recorder, question: str, gathered: Gathered, llm, intent: Intent | None = None
) -> None:
    """Write the reply from what was gathered — and only from that."""
    with recorder.step("answer", tool="llm" if llm else "structured") as event:
        if llm is None:
            recorder.trace.answer = _structured_answer(gathered)
        else:
            recorder.trace.answer = _model_answer(llm, question, gathered, intent)
        recorder.trace.cited_ids = gathered.all_ids
        event.node_ids = gathered.all_ids
        event.summary = f"answered from {len(gathered.all_ids)} entity(ies)"


def _critique(recorder: Recorder, session, pack: str, gathered: Gathered) -> None:
    """Check the answer against the graph.

    Not a model asking itself whether it did well — a lookup. Every identifier
    the answer rests on came from a query, so any that the graph cannot produce
    was invented somewhere between the query and the sentence.
    """
    with recorder.step("critique", tool="cypher") as event:
        present, missing = verify(session, pack, recorder.trace.cited_ids)
        event.node_ids = present
        event.detail = {"verified": len(present), "unverifiable": missing}
        event.summary = (
            f"all {len(present)} cited entity(ies) exist in the graph"
            if not missing
            else f"{len(missing)} cited entity(ies) are not in the graph: {', '.join(missing[:3])}"
        )
        if missing:
            recorder.trace.cited_ids = present


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


#: How many substrings of one question are worth a lookup each.
CANDIDATE_LIMIT = 16


def _candidates(question: str) -> list[str]:
    """Substrings of the question worth looking up.

    Quoted spans first — a question that says `urllib3` in backticks is naming
    something. Then runs of words, then bare tokens, including identifier-like
    runs such as 2025/717 or 4857 that a word tokeniser would break apart.

    The budget is what makes the ordering delicate. Word runs come first because
    an entity is often named in several — "Sendikalar Kanunu" is one statute and
    neither half identifies anything — but a seven-word question generates
    thirteen runs, and with a flat cap they crowded out every single token.
    "What bugs have been reported that mention httpx?" produced no candidate
    named `httpx` at all: the lookup fell through to fuzzy matching on the word
    *reported*, resolved it to the package `reporters-db`, and answered about
    that instead.

    So single tokens are reserved a share of the budget rather than appended to
    whatever is left. Runs still go first, and the whole list is tried, so the
    ordering only decides what survives the cap.
    """
    quoted = [s.strip() for s in re.findall(r"[`\'\"]([^`\'\"]{2,60})[`\'\"]", question)]

    tokens = re.findall(r"\d{4}/\d+|\b\d{3,4}\b|[^\W\d_][\w.\-]{1,}", question, re.UNICODE)
    runs = [
        " ".join(tokens[start : start + size])
        for size in (3, 2)
        for start in range(len(tokens) - size + 1)
        if any(t.lower() not in _NOISE for t in tokens[start : start + size])
    ]
    singles = [t for t in tokens if t.lower() not in _NOISE]

    budget = max(CANDIDATE_LIMIT - len(quoted), 0)
    kept_singles = singles[: max(budget // 2, 1)] if singles else []
    kept_runs = runs[: budget - len(kept_singles)]

    seen: list[str] = []
    for item in [*quoted, *kept_runs, *kept_singles]:
        if item and item not in seen:
            seen.append(item)
    return seen[:CANDIDATE_LIMIT]


def _structured_answer(gathered: Gathered) -> str:
    """The answer when no model is available: what was found, plainly.

    Not a placeholder. A traversal result is often the whole answer, and saying
    it without a model is both faster and impossible to hallucinate.
    """
    if not gathered.entities:
        return "The question does not name anything the graph holds."
    subject = ", ".join(e.name for e in gathered.entities[:3])
    if not gathered.neighbours:
        return f"Found {subject}, but nothing reachable from it for this question."
    names = [n.name for n in gathered.neighbours]
    listed = ", ".join(names[:15])
    more = f", and {len(names) - 15} more" if len(names) > 15 else ""
    return f"From {subject}: {len(names)} result(s) — {listed}{more}."


def _model_answer(llm, question: str, gathered: Gathered, intent: Intent | None = None) -> str:
    """Ask the model to write the reply, from the gathered material only.

    The traversal result is labelled with what produced it, and that is not
    cosmetic. Given a bare list under "reached by traversing the graph", models
    read the passages as "the provided text" and answer that the question cannot
    be answered — observed on "what would break if urllib3 broke" with sixty
    correct entities sitting in the prompt. The list *is* the answer to a
    graph-shaped question; no passage says it, which is the entire reason the
    traversal ran. So the prompt says which relation was followed and that the
    result is a fact from the graph rather than context around it.
    """
    lines = [f"Question: {question}", ""]
    if gathered.entities:
        lines.append(
            "Entities the question names: "
            + ", ".join(f"{e.name} ({e.id})" for e in gathered.entities[:10])
        )
    if gathered.neighbours:
        what = f" ({intent.description.rstrip('.')})" if intent and intent.description else ""
        lines.append(
            f"Result of following '{intent.name if intent else 'the graph'}'{what} in the "
            "knowledge graph. These are established facts, not passages to interpret, and "
            "no document is expected to state them:"
        )
        lines.append(", ".join(f"{n.name} ({n.id})" for n in gathered.neighbours[:40]))
        if len(gathered.neighbours) > 40:
            lines.append(f"...and {len(gathered.neighbours) - 40} more.")
    for passage in gathered.passages[:4]:
        lines.append(f"Passage: {passage.text[:600]}")

    lines += [
        "",
        "Answer the question using only what is above. Name the entities you rely on.",
        "The graph result above answers structural questions on its own — do not say the "
        "question cannot be answered merely because no passage repeats it.",
        "If what is above genuinely does not answer it, say so rather than filling the gap.",
    ]
    try:
        return str(llm.complete("\n".join(lines))).strip()
    except Exception as exc:
        logger.warning("The model did not answer, falling back to the structured reply — %s", exc)
        return _structured_answer(gathered)


def _ask_model_to_route(llm, question: str, rules: RetrievalRules) -> Intent | None:
    """Let the model pick an intent when the pack's phrases did not."""
    catalogue = "\n".join(f"- {i.name}: {i.description}" for i in rules.intents)
    prompt = (
        f"Question: {question}\n\nWhich of these best fits?\n{catalogue}\n"
        "- none: nothing fits\n\nAnswer with the name alone."
    )
    try:
        reply = str(llm.complete(prompt)).strip().lower()
    except Exception as exc:
        logger.warning("Routing by model failed — %s", exc)
        return None
    for intent in rules.intents:
        if intent.name.lower() in reply:
            return intent
    return None
