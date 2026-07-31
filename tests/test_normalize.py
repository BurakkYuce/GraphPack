"""Normalisers and templates.

These decide what a node's identity is, so a change here silently re-partitions
the graph. Golden cases from real records, not invented ones.
"""

from __future__ import annotations

import pytest

from graphpack.backbone.normalize import (
    NormalizeError,
    build_pipelines,
    field,
    referenced_pipelines,
    render,
)

pytestmark = pytest.mark.unit

# The oss pack's declarations, kept here verbatim so a change to either side
# shows up as a failing test rather than as a differently-shaped graph.
PYPI_NAME = [
    "strip",
    "lower",
    {"regex_replace": {"pattern": "[-_.]+", "replace": "-"}},
]
REQUIREMENT_NAME = [
    "strip",
    {"regex_extract": {"pattern": "^([A-Za-z0-9][A-Za-z0-9._-]*)"}},
    "lower",
    {"regex_replace": {"pattern": "[-_.]+", "replace": "-"}},
]
REPO_SLUG = [
    "strip",
    "lower",
    {"regex_extract": {"pattern": r"github\.com[:/]+([^/]+/[^/#?]+)"}},
    {"regex_replace": {"pattern": r"\.git$", "replace": ""}},
]


@pytest.fixture
def pipelines():
    return build_pipelines(
        {"pypi_name": PYPI_NAME, "requirement_name": REQUIREMENT_NAME, "repo_slug": REPO_SLUG}
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("urllib3", "urllib3"),
        ("PyYAML", "pyyaml"),
        ("typing_extensions", "typing-extensions"),
        ("zope.interface", "zope-interface"),
        ("ruamel.yaml.clib", "ruamel-yaml-clib"),
        ("  Flask-SQLAlchemy  ", "flask-sqlalchemy"),
        ("backports.tarfile", "backports-tarfile"),
    ],
)
def test_package_names_collapse_to_one_form(pipelines, raw, expected):
    """PEP 503: lowercase, and runs of -, _ and . become a single -.

    Without this, `typing_extensions` and `typing-extensions` are two packages
    and half the dependency edges point at a node nobody else references.
    """
    assert pipelines["pypi_name"](raw) == expected


@pytest.mark.parametrize(
    ("requirement", "expected"),
    [
        ("urllib3<3,>=1.26", "urllib3"),
        ("charset_normalizer<4,>=2", "charset-normalizer"),
        ("idna<4,>=2.5", "idna"),
        ("certifi>=2023.5.7", "certifi"),
        ("botocore[crt]<2.0a0,>=1.21.0", "botocore"),
        ('importlib-metadata; python_version < "3.10"', "importlib-metadata"),
        ("PySocks!=1.5.7,>=1.5.6", "pysocks"),
        ("typing-extensions ~= 4.0", "typing-extensions"),
    ],
)
def test_requirement_strings_reduce_to_a_distribution_name(pipelines, requirement, expected):
    """Dependencies arrive as PEP 508 strings; the edge needs only the name."""
    assert pipelines["requirement_name"](requirement) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/urllib3/urllib3", "urllib3/urllib3"),
        ("https://github.com/psf/requests/", "psf/requests"),
        ("https://github.com/pypa/packaging.git", "pypa/packaging"),
        ("git@github.com:numpy/numpy.git", "numpy/numpy"),
        # A link to any page inside a repository still identifies the repository.
        ("https://github.com/python/typing_extensions/issues", "python/typing_extensions"),
        ("https://github.com/python/cpython/blob/main/README.rst", "python/cpython"),
        ("https://GitHub.com/Textualize/rich#readme", "textualize/rich"),
        # Not a repository at all.
        ("https://pyyaml.org/", ""),
        ("https://gitlab.com/takluyver/jeepney", ""),
    ],
)
def test_repository_urls_reduce_to_owner_and_repo(pipelines, url, expected):
    assert pipelines["repo_slug"](url) == expected


def test_first_populated_field_wins(pipelines):
    """Publishers record the same fact in whichever field they filled in."""
    row = {"a": None, "b": "https://github.com/psf/requests"}

    assert render("gh:{a,b|repo_slug}", row, pipelines) == "gh:psf/requests"


def test_a_field_that_normalises_to_nothing_falls_through(pipelines):
    """A homepage survives the field lookup but not the extraction; the next
    candidate is what makes the difference between a repository and none."""
    row = {"home": "https://pyyaml.org/", "source": "https://github.com/yaml/pyyaml"}

    assert render("gh:{home,source|repo_slug}", row, pipelines) == "gh:yaml/pyyaml"


def test_missing_fields_render_empty_rather_than_raising(pipelines):
    """Half-complete records are normal in published data. The loader decides
    what to do with an incomplete id; rendering is not the place to fail."""
    assert render("gh:{absent|repo_slug}", {}, pipelines) == "gh:"


def test_dotted_paths_reach_into_nested_records(pipelines):
    row = {"info": {"project_urls": {"Source": "https://github.com/psf/requests"}}}

    assert render("{info.project_urls.Source|repo_slug}", row, pipelines) == "psf/requests"


def test_unknown_pipeline_is_an_error(pipelines):
    """Silently ignoring it would build ids from unnormalised values."""
    with pytest.raises(NormalizeError, match="undefined normalize pipeline 'nope'"):
        render("{name|nope}", {"name": "x"}, pipelines)


def test_unknown_operation_names_the_alternatives():
    with pytest.raises(NormalizeError, match="unknown operation 'titlecase'"):
        build_pipelines({"broken": ["titlecase"]})


def test_field_returns_none_for_a_path_through_a_scalar():
    assert field({"a": "text"}, "a.b") is None


def test_referenced_pipelines_finds_every_candidate():
    assert referenced_pipelines("gh:{a,b|repo_slug}/{c|other}") == {"repo_slug", "other"}
