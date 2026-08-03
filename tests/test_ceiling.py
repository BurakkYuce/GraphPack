"""The containment ceiling: what chunking makes unscoreable before retrieval runs.

Written because this measurement was retyped as a throwaway script twice and
refuted a hypothesis both times — first that chunk boundaries explained the gap
to MultiHop-RAG's published table (they did not, at 1024 tokens), then that the
paper's own 256-token chunks would close it (they did not; they cost 86 of 981
sentences outright).
"""

from __future__ import annotations

import pytest

from graphpack.bench.ceiling import Ceiling, measure, normalise


@pytest.mark.unit
def test_normalise_survives_the_whitespace_chunking_introduces():
    """Evidence is quoted verbatim, so it is present — but not byte-identical.

    Chunking and any prepended metadata leave newlines and runs of spaces where
    the source had none. Matching raw strings loses real hits to a line break.
    """
    fact = "The court  cited\n Article 5."
    chunk = "…text. The COURT cited Article 5. More text…"

    assert normalise(fact) in normalise(chunk)


@pytest.mark.unit
def test_a_sentence_split_across_a_boundary_is_not_present(monkeypatch):
    """The whole point: this miss belongs to the splitter, not the retriever."""
    monkeypatch.setattr(
        "graphpack.bench.ceiling._chunk_texts",
        lambda _c: ["… the court cited", "Article 5 of the code …"],
    )

    result = measure("any", {"the court cited Article 5 of the code"})

    assert result.present == 0
    assert result.ratio == 0.0


@pytest.mark.unit
def test_a_sentence_inside_one_chunk_is_present(monkeypatch):
    monkeypatch.setattr(
        "graphpack.bench.ceiling._chunk_texts",
        lambda _c: ["preamble … the court cited Article 5 of the code … more"],
    )

    result = measure("any", {"the court cited Article 5 of the code"})

    assert result.present == 1
    assert result.ratio == 1.0


@pytest.mark.unit
def test_an_empty_collection_reports_zero_rather_than_raising(monkeypatch):
    """A pack that has not been ingested is a state to report, not a traceback."""
    monkeypatch.setattr("graphpack.bench.ceiling._chunk_texts", lambda _c: [])

    result = measure("any", {"anything"})

    assert result.chunks == 0
    assert result.present == 0


@pytest.mark.unit
def test_blank_facts_do_not_inflate_the_denominator(monkeypatch):
    """A gold row with an empty `fact` is a row without evidence, not a miss."""
    monkeypatch.setattr("graphpack.bench.ceiling._chunk_texts", lambda _c: ["a real sentence here"])

    result = measure("any", {"a real sentence here", "   ", ""})

    assert result.facts == 1
    assert result.ratio == 1.0


@pytest.mark.unit
def test_the_line_says_what_the_number_bounds():
    line = Ceiling(chunks=7_327, facts=981, present=895).line()

    assert "895" in line and "981" in line and "7,327" in line
    # The phrase matters as much as the figure: read without it, a ceiling looks
    # like a score.
    assert "ceiling on evidence recall" in line
    assert "91.2%" in line
