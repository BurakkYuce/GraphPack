"""Mention to canonical identifier.

The method a mention resolved by matters as much as whether it resolved: an
exact match is a fact, a fuzzy match is a guess. These tests pin the difference,
and the real pack's templates are exercised against mention shapes taken from
actual issue threads.
"""

from __future__ import annotations

import textwrap

import pytest

from graphpack.resolve.contract import ResolveError, load_rules
from graphpack.resolve.methods import BackboneIndex, alias_key, apply

pytestmark = pytest.mark.unit

RULES = textwrap.dedent(
    """\
    normalize:
      pkg:
        - strip
        - lower
        - {regex_extract: {pattern: "^([A-Za-z0-9][A-Za-z0-9._-]*)"}}
        - {regex_replace: {pattern: "[-_.]+", replace: "-"}}

    resolve:
      - entity: PACKAGE
        target: Package
        id: "pypi:{name|pkg}"
        match: "{name|pkg}"
        methods: [exact, alias, fuzzy]
        fuzzy_threshold: 93
        on_unresolved: provisional
    """
)


class FakeIndex(BackboneIndex):
    """A backbone without a database behind it."""

    def __init__(self, ids: set[str], forms: dict[str, str]):
        self._ids = {"Package": ids}
        self._match_forms = {"Package": forms}
        self.aliases: dict[str, str] = {}


@pytest.fixture
def rules(tmp_path):
    def _make(body: str = RULES, aliases: str | None = None):
        (tmp_path / "resolve.yaml").write_text(body, encoding="utf-8")
        alias_path = tmp_path / "aliases.csv"
        if aliases is not None:
            alias_path.write_text(aliases, encoding="utf-8")
        return load_rules(tmp_path / "resolve.yaml", alias_path)

    return _make


@pytest.fixture
def index():
    return FakeIndex(
        ids={"pypi:urllib3", "pypi:requests", "pypi:pillow", "pypi:typing-extensions"},
        forms={
            "urllib3": "pypi:urllib3",
            "requests": "pypi:requests",
            "pillow": "pypi:pillow",
            "typing-extensions": "pypi:typing-extensions",
        },
    )


def _resolve(text, rules, index):
    from graphpack.resolve.pipeline import _resolve_one

    return _resolve_one(text, rules.rules[0], rules, index)


# ----------------------------------------------------------------------
# Methods
# ----------------------------------------------------------------------


@pytest.mark.parametrize("mention", ["urllib3", "urllib3 2.0", "urllib3<2", "  URLLIB3  "])
def test_a_mention_that_normalises_to_an_identifier_matches_exactly(mention, rules, index):
    """Decoration is what varies between how a thread writes a package and what
    the index calls it. Which decoration a pack strips is its own decision —
    the real pack's handling of backticks and extras is tested further down."""
    match = _resolve(mention, rules(), index)

    assert match is not None
    assert (match.canonical_id, match.method) == ("pypi:urllib3", "exact")


def test_underscores_and_dots_collapse_the_way_the_backbone_collapses_them(rules, index):
    """Both sides apply PEP 503, so `typing_extensions` and `typing-extensions`
    are one package rather than two."""
    match = _resolve("typing_extensions", rules(), index)

    assert match.canonical_id == "pypi:typing-extensions"


def test_an_alias_resolves_what_no_string_distance_would(rules, index):
    """`PIL` is Pillow's import name. Nothing about the two strings says so."""
    loaded = rules(aliases="entity,surface,id\nPACKAGE,PIL,pypi:pillow\n")
    index.aliases = loaded.aliases

    match = _resolve("PIL", loaded, index)

    assert (match.canonical_id, match.method) == ("pypi:pillow", "alias")


def test_exact_wins_over_alias_and_fuzzy(rules, index):
    """Methods are listed most trustworthy first, so an earlier answer is a
    better answer rather than merely a sooner one."""
    loaded = rules(aliases="entity,surface,id\nPACKAGE,requests,pypi:urllib3\n")
    index.aliases = loaded.aliases

    match = _resolve("requests", loaded, index)

    assert (match.canonical_id, match.method) == ("pypi:requests", "exact")


def test_fuzzy_catches_a_typo(rules, index):
    match = _resolve("urllib33", rules(), index)

    assert match is not None
    assert (match.canonical_id, match.method) == ("pypi:urllib3", "fuzzy")
    assert match.score < 100


def test_fuzzy_refuses_a_different_package(rules, index):
    """The threshold exists because short names score high against each other.
    Inventing a dependency is worse than leaving a mention unplaced."""
    match = _resolve("aiohttp", rules(), index)

    assert match is None or match.canonical_id != "pypi:requests"


def test_an_alias_pointing_outside_the_backbone_is_refused(rules, index):
    """A stale alias row should fail to resolve, not fabricate an edge to a node
    that does not exist."""
    loaded = rules(aliases="entity,surface,id\nPACKAGE,ghost,pypi:not-loaded\n")
    index.aliases = loaded.aliases

    match = _resolve("ghost", loaded, index)

    assert match is None or match.canonical_id != "pypi:not-loaded"


# ----------------------------------------------------------------------
# Contract
# ----------------------------------------------------------------------


def test_fuzzy_without_a_match_template_is_rejected(tmp_path):
    """Comparing the identifier template against bare names measures the wrong
    thing, and Jaro-Winkler rewards the shared `pypi:` prefix specifically."""
    (tmp_path / "resolve.yaml").write_text(
        'resolve:\n  - {entity: P, target: T, id: "x:{name}", methods: [fuzzy]}\n',
        encoding="utf-8",
    )

    with pytest.raises(ResolveError, match="needs a 'match' template"):
        load_rules(tmp_path / "resolve.yaml")


def test_a_meaningless_fuzzy_threshold_is_rejected(tmp_path):
    (tmp_path / "resolve.yaml").write_text(
        'resolve:\n  - entity: P\n    target: T\n    id: "x:{name}"\n'
        '    match: "{name}"\n    methods: [fuzzy]\n    fuzzy_threshold: 40\n',
        encoding="utf-8",
    )

    with pytest.raises(ResolveError, match="too low to be meaningful"):
        load_rules(tmp_path / "resolve.yaml")


def test_two_rules_for_one_entity_are_rejected(tmp_path):
    """Only the first would ever apply, so the second is a silent mistake."""
    (tmp_path / "resolve.yaml").write_text(
        'resolve:\n  - {entity: P, target: A, id: "a:{name}"}\n'
        '  - {entity: P, target: B, id: "b:{name}"}\n',
        encoding="utf-8",
    )

    with pytest.raises(ResolveError, match="more than one rule"):
        load_rules(tmp_path / "resolve.yaml")


def test_an_unknown_method_names_the_alternatives(tmp_path):
    (tmp_path / "resolve.yaml").write_text(
        'resolve:\n  - {entity: P, target: T, id: "x:{name}", methods: [telepathy]}\n',
        encoding="utf-8",
    )

    with pytest.raises(ResolveError, match="unknown method"):
        load_rules(tmp_path / "resolve.yaml")


def test_alias_rows_are_normalised_the_way_mentions_are(rules):
    """Otherwise the table and the lookup disagree about what a surface form
    looks like, and every entry silently misses."""
    loaded = rules(aliases="entity,surface,id\nPACKAGE,  PIL  ,pypi:pillow\n")

    assert alias_key("PACKAGE", "pil") in loaded.aliases


def test_alias_comments_are_not_read_as_rows(rules):
    loaded = rules(aliases="entity,surface,id\n# why this table exists\nPACKAGE,PIL,pypi:pillow\n")

    assert len(loaded.aliases) == 1


def test_an_alias_for_an_entity_with_no_rule_is_rejected(rules):
    """A typo in the entity column would otherwise make the row disappear."""
    with pytest.raises(ResolveError, match="no resolve rule for entity"):
        rules(aliases="entity,surface,id\nPACKAGES,PIL,pypi:pillow\n")


# ----------------------------------------------------------------------
# The real pack
# ----------------------------------------------------------------------


@pytest.fixture
def oss():
    from graphpack.packs import load_pack

    pack = load_pack("oss")
    return load_rules(pack.path("resolve.yaml"), pack.path("aliases.csv"))


@pytest.mark.parametrize(
    ("mention", "expected"),
    [
        ("urllib3", "pypi:urllib3"),
        ("urllib3 2.0", "pypi:urllib3"),
        ("celery[redis]", "pypi:celery"),
        ("`requests`", "pypi:requests"),
        ("PyYAML", "pypi:pyyaml"),
        ("typing_extensions", "pypi:typing-extensions"),
    ],
)
def test_the_pack_strips_the_decoration_threads_write(oss, mention, expected):
    rule = oss.for_entity("PACKAGE")

    assert apply(rule.id, mention, oss.pipelines) == expected


@pytest.mark.parametrize(
    ("mention", "expected"),
    [
        ("boto/boto3", "gh:boto/boto3"),
        ("https://github.com/psf/requests/issues", "gh:psf/requests"),
        ("https://github.com/urllib3/urllib3.git", "gh:urllib3/urllib3"),
        ("git@github.com:numpy/numpy.git", "gh:numpy/numpy"),
        # No pair at all — nothing to build an identifier from.
        ("requests", ""),
    ],
)
def test_the_pack_finds_the_repository_in_a_url_not_the_host(oss, mention, expected):
    """A URL contains two `x/y` pairs and the host is the wrong one."""
    rule = oss.for_entity("REPOSITORY")

    assert apply(rule.id, mention, oss.pipelines) == expected


@pytest.mark.parametrize(
    ("mention", "expected"),
    [
        ("urllib3 2.0", "pypi:urllib3@2.0"),
        ("requests 2.31.0", "pypi:requests@2.31.0"),
        # A release identifier needs both halves; a bare package names no release.
        ("urllib3", ""),
    ],
)
def test_a_release_needs_both_a_package_and_a_version(oss, mention, expected):
    rule = oss.for_entity("RELEASE")

    assert apply(rule.id, mention, oss.pipelines) == expected


# ----------------------------------------------------------------------
# Resolving through a relation extraction claimed
# ----------------------------------------------------------------------


def _with_context(tmp_path, context: str):
    (tmp_path / "resolve.yaml").write_text(
        textwrap.dedent(
            f"""\
            normalize:
              number: [{{regex_extract: {{pattern: "(\\\\d+)"}}}}]
            resolve:
              - entity: ARTICLE
                target: Article
                id: "madde:{{name}}"
                context: {context}
            """
        ),
        encoding="utf-8",
    )
    return load_rules(tmp_path / "resolve.yaml")


def test_a_context_block_is_parsed(tmp_path):
    rules = _with_context(
        tmp_path, '{via: HAS_ARTICLE, from: STATUTE, id: "madde:{source}/{name|number}"}'
    )

    context = rules.rules[0].context
    assert (context.via, context.source) == ("HAS_ARTICLE", "STATUTE")


def test_a_context_id_that_ignores_the_source_is_rejected(tmp_path):
    """Without `{source}` this builds the same identifier the rule's own methods
    already failed on, so it would resolve nothing and look like a feature."""
    with pytest.raises(ResolveError, match="must use '{source}'"):
        _with_context(tmp_path, '{via: HAS_ARTICLE, from: STATUTE, id: "madde:{name}"}')


def test_a_pipeline_on_the_source_counts_as_using_it(tmp_path):
    """`{source|statute_number}` is the ordinary form — the canonical id is
    `kanun:5718` and the identifier wants 5718. An earlier version of this check
    matched the bare `{source}` only, and an earlier version of the *renderer*
    substituted it by string replacement, which silently resolved 0 of 543
    mentions where 282 were available."""
    rules = _with_context(
        tmp_path, '{via: HAS_ARTICLE, from: STATUTE, id: "madde:{source|number}/{name|number}"}'
    )

    assert rules.rules[0].context.id == "madde:{source|number}/{name|number}"


def test_context_needs_all_three_keys(tmp_path):
    with pytest.raises(ResolveError, match="context is missing"):
        _with_context(tmp_path, "{via: HAS_ARTICLE, from: STATUTE}")


def test_context_rejects_keys_it_does_not_know(tmp_path):
    with pytest.raises(ResolveError, match="unknown key"):
        _with_context(
            tmp_path,
            '{via: HAS_ARTICLE, from: STATUTE, id: "madde:{source}", threshold: 90}',
        )


def test_a_pipeline_used_only_by_the_context_template_must_exist(tmp_path):
    (tmp_path / "resolve.yaml").write_text(
        'resolve:\n  - {entity: A, target: T, id: "x:{name}", '
        'context: {via: V, from: S, id: "x:{source|nope}"}}\n',
        encoding="utf-8",
    )

    with pytest.raises(ResolveError, match="undefined normalize pipeline"):
        load_rules(tmp_path / "resolve.yaml")
