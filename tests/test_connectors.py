"""Named data sources in a pack's fetch block.

The engine ships readers for fourteen systems. What is tested here is the seam:
that a pack can name one, that naming it wrongly fails at validation rather than
at the end of a fetch, and that what comes back is *rows* — because every later
block in the contract reads rows, and the corpus `id` template in particular is
what joins the graph to the documents.
"""

from __future__ import annotations

import textwrap

import pytest

from graphpack.backbone.connectors import ConnectorError, check_connector, expand
from graphpack.backbone.sources import SourcesError, load_sources

pytestmark = pytest.mark.unit


def _sources(tmp_path, body: str):
    (tmp_path / "sources.yaml").write_text(textwrap.dedent(body), encoding="utf-8")
    return load_sources(tmp_path / "sources.yaml")


def test_a_fetch_step_may_name_a_connector_instead_of_a_url(tmp_path):
    sources = _sources(
        tmp_path,
        """\
        fetch:
          - id: notes
            source: filesystem
            config: {paths: ["/tmp/x"]}
            out: notes.jsonl
        """,
    )

    assert sources.fetch[0].source == "filesystem"
    assert sources.fetch[0].url == ""


def test_a_step_needs_one_of_url_or_source(tmp_path):
    with pytest.raises(SourcesError, match="needs either 'url' or 'source'"):
        _sources(tmp_path, "fetch:\n  - {id: a, out: a.jsonl}\n")


def test_a_step_cannot_have_both(tmp_path):
    """They acquire from different places, and a step that names both has not
    decided which."""
    with pytest.raises(SourcesError, match="has both 'url' and 'source'"):
        _sources(
            tmp_path,
            "fetch:\n  - {id: a, out: a.jsonl, url: 'https://x.invalid', source: filesystem}\n",
        )


def test_an_unknown_connector_is_rejected_with_the_list(tmp_path):
    """At `packs validate`, which is the command a pack author runs before
    anything is reachable — not after a fetch has already spent its time."""
    with pytest.raises(SourcesError, match="unknown source 'telepathy'"):
        _sources(tmp_path, "fetch:\n  - {id: a, out: a.jsonl, source: telepathy}\n")


def test_a_connector_missing_its_required_config_is_rejected(tmp_path):
    with pytest.raises(SourcesError, match=r"needs config key\(s\) \['bucket'\]"):
        _sources(tmp_path, "fetch:\n  - {id: a, out: a.jsonl, source: s3, config: {}}\n")


def test_config_belongs_to_a_source_step(tmp_path):
    with pytest.raises(SourcesError, match="'config' belongs to a 'source' step"):
        _sources(
            tmp_path,
            "fetch:\n  - {id: a, out: a.jsonl, url: 'https://x.invalid', config: {k: v}}\n",
        )


def test_credentials_come_from_the_environment(monkeypatch):
    """The same bargain as request headers: the pack names the variable, the
    machine holds the value."""
    monkeypatch.setenv("GRAPHPACK_TEST_TOKEN", "s3cret")

    assert expand({"key": "${GRAPHPACK_TEST_TOKEN}", "n": 3}) == {"key": "s3cret", "n": 3}


def test_an_unset_variable_expands_to_empty_rather_than_raising():
    """So the reader reports its own "no credentials" error, which says more
    about what is actually missing than ours would."""
    assert expand({"key": "${GRAPHPACK_TEST_ABSENT}"}) == {"key": ""}


def test_every_declared_connector_names_a_class_the_engine_has():
    """The registry is a hand-written map, so it can drift from the engine. This
    is what notices."""
    import sources as engine_sources

    from graphpack.backbone.connectors import CONNECTORS

    missing = [
        (name, cls) for name, (cls, _) in CONNECTORS.items() if not hasattr(engine_sources, cls)
    ]
    assert not missing, f"connectors naming classes the engine does not export: {missing}"


def test_check_connector_is_static():
    """No network, no credentials, no import of the reader — it runs inside
    `packs validate`."""
    check_connector("wikipedia", {"query": "anything"}, "where")

    with pytest.raises(ConnectorError):
        check_connector("wikipedia", {}, "where")
