"""The resolution methods themselves.

Ordered by precision. Each takes a mention and returns a canonical identifier or
nothing; the pipeline tries them in the order the pack listed and stops at the
first answer, recording which one gave it. That record is the point — a
resolution rate means little without knowing how much of it was guessed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from graphpack.backbone.normalize import Pipeline, render_parts

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Match:
    """One resolved mention."""

    canonical_id: str
    method: str
    #: 100 for exact and alias matches; the similarity score for fuzzy ones.
    score: int = 100


def apply(template: str, text: str, pipelines: dict[str, Pipeline]) -> str:
    """Render a rule template against a piece of text, or return nothing.

    The text is offered as both ``{name}`` and ``{text}`` so a pack can use
    whichever reads better; they are the same value.

    A template with more than one placeholder can be left incomplete by a
    mention that supplies only some of them: ``"pypi:{name|mention_name}@{name|
    version_only}"`` against a bare "urllib3" renders ``"pypi:urllib3@"``. That
    is not an identifier, and it is not a near miss either — it is the mention
    failing to say which release it meant. Empty is the honest answer.
    """
    if not template:
        return ""
    rendered, complete = render_parts(template, {"name": text, "text": text}, pipelines)
    return rendered.strip() if complete else ""


def alias_key(entity: str, normalised: str) -> str:
    """Key for the alias table. Entity-scoped, because `PIL` means one thing as
    a package and could mean another as a repository."""
    return f"{entity}\x00{normalised}"


def exact(text: str, rule, pipelines: dict[str, Pipeline], index: BackboneIndex) -> Match | None:
    """The mention, normalised, is already a canonical identifier.

    The high-precision path, and for this domain the common one: a package name
    written in prose usually *is* the package name.
    """
    candidate = apply(rule.id, text, pipelines)
    if candidate and index.has(rule.target, candidate):
        return Match(candidate, "exact")
    return None


def alias(text: str, rule, pipelines: dict[str, Pipeline], index: BackboneIndex) -> Match | None:
    """A known surface form maps to an identifier it does not resemble.

    `PIL` is `pillow`, `sklearn` is `scikit-learn`, `cv2` is `opencv-python`. No
    amount of string distance finds those, which is why the table is written by
    hand and grown from the mentions resolution failed on.
    """
    probe = apply(rule.match or rule.id, text, pipelines)
    canonical = index.aliases.get(alias_key(rule.entity, probe))
    if canonical and index.has(rule.target, canonical):
        return Match(canonical, "alias")
    return None


def fuzzy(text: str, rule, pipelines: dict[str, Pipeline], index: BackboneIndex) -> Match | None:
    """Nearest backbone name above the pack's threshold.

    Both sides go through the rule's ``match`` template first, so the comparison
    is between two strings of the same shape. This is the last resort and the
    one that can be wrong: short identifiers score misleadingly high against
    each other, which is why the threshold is a pack decision.
    """
    from rapidfuzz import process
    from rapidfuzz.distance import JaroWinkler

    candidates = index.match_forms(rule.target)
    if not candidates:
        return None

    probe = apply(rule.match, text, pipelines)
    if not probe:
        return None

    best = process.extractOne(
        probe,
        candidates.keys(),
        scorer=JaroWinkler.similarity,
        score_cutoff=rule.fuzzy_threshold / 100,
    )
    if best is None:
        return None
    form, score, _ = best
    return Match(candidates[form], "fuzzy", int(round(score * 100)))


METHODS = {"exact": exact, "alias": alias, "fuzzy": fuzzy}


class BackboneIndex:
    """The canonical side of the join, held in memory.

    Resolution asks "is this an identifier" and "what is the closest name" once
    per mention. Against the database that is two round trips each, and the
    fuzzy question has no index to use at all. The backbone is small enough that
    reading it once is both faster and simpler.
    """

    def __init__(self, session, pack: str, rules, pipelines: dict[str, Pipeline]):
        self._ids: dict[str, set[str]] = {}
        self._match_forms: dict[str, dict[str, str]] = {}
        self.aliases: dict[str, str] = {}

        for rule in rules:
            rows = session.run(
                f"MATCH (n:{rule.target} {{pack: $pack}}) RETURN n.id AS id, n[$field] AS name",
                pack=pack,
                field=rule.fuzzy_field,
            )
            ids: set[str] = set()
            forms: dict[str, str] = {}
            for row in rows:
                identifier = row["id"]
                if not identifier:
                    continue
                ids.add(identifier)
                name = row["name"]
                if not name or not rule.match:
                    continue
                # Normalise the backbone name exactly as a mention will be, so
                # the fuzzy comparison runs between like and like.
                form = apply(rule.match, str(name), pipelines)
                # Names are not unique across packages; the first wins, and the
                # ambiguity stays out of the scoring rather than being resolved
                # arbitrarily on every lookup.
                if form and form not in forms:
                    forms[form] = identifier
            self._ids[rule.target] = ids
            self._match_forms[rule.target] = forms
            logger.info(
                "Indexed %d %s nodes (%d distinct match forms)", len(ids), rule.target, len(forms)
            )

    def has(self, label: str, identifier: str) -> bool:
        return identifier in self._ids.get(label, ())

    def match_forms(self, label: str) -> dict[str, str]:
        return self._match_forms.get(label, {})

    def size(self, label: str) -> int:
        return len(self._ids.get(label, ()))
