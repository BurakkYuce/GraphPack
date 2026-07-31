"""The architectural rules, checked as tests rather than trusted as habit.

The project's whole claim is that a new vertical costs configuration and no
code.  That claim is only worth anything if it is enforced, so the layering
rules run alongside everything else instead of living in a README.

CI runs these too; keeping them here means a violation shows up while writing
the code rather than after pushing it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from graphpack.packs.contract import list_packs
from graphpack.paths import REPO_ROOT

pytestmark = pytest.mark.unit

CODE_ROOT = REPO_ROOT / "graphpack"
DOMAINS_ROOT = REPO_ROOT / "domains"

#: Top-level names the engine occupies (pyproject py-modules + packages).
#: A module of ours with any of these names would shadow the engine's and break
#: imports in ways that surface far from the cause.
ENGINE_TOP_LEVEL = frozenset(
    {
        "backend",
        "config",
        "factories",
        "flow_service",
        "hybrid_system",
        "incremental_system",
        "main",
        "post_ingestion_state",
        "query_engine",
        "retriever_setup",
        "schema_manager",
        "start",
        "adapters",
        "ingest",
        "process",
        "sources",
        "observability",
        "incremental_updates",
        "rdf",
        "langchain",
        "stores",
        "llamaindex",
        "langflow_components",
    }
)


def _python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def test_domains_contain_no_python():
    """A pack is data. The moment one ships code, "config only" stops being true."""
    offenders = [p.relative_to(REPO_ROOT) for p in DOMAINS_ROOT.rglob("*.py")]

    assert offenders == [], f"packs must contain no Python: {offenders}"


def test_code_never_imports_domains():
    """Direction of dependency: code reads packs at runtime, never imports them."""
    offenders = []
    for path in _python_files(CODE_ROOT):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name == "domains" or name.startswith("domains.") for name in names):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")

    assert offenders == [], f"graphpack must not import domains: {offenders}"


def test_no_pack_name_is_hard_coded_in_code():
    """The generality claim in one assertion.

    Matches quoted pack names only — a substring search would fire on "cross",
    "across" and "loss" for a pack called "oss", which is exactly the kind of
    false positive that gets a check disabled.
    """
    names = list_packs()
    if not names:
        pytest.skip("no packs to check")

    pattern = re.compile(r"""['"](?:{})['"]""".format("|".join(re.escape(n) for n in names)))
    offenders = []
    for path in _python_files(CODE_ROOT):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")

    assert offenders == [], (
        "pack names must not appear in code — move the value into the pack:\n"
        + "\n".join(offenders)
    )


def test_no_top_level_module_shadows_the_engine():
    """The engine installs flat top-level modules; ours all live under graphpack/."""
    ours = {
        p.stem if p.is_file() else p.name
        for p in REPO_ROOT.iterdir()
        if (p.suffix == ".py") or (p.is_dir() and (p / "__init__.py").is_file())
    }

    assert not (ours & ENGINE_TOP_LEVEL), (
        f"these top-level names shadow engine modules: {sorted(ours & ENGINE_TOP_LEVEL)}"
    )


def test_every_pack_directory_is_a_valid_pack():
    """A directory under domains/ without a pack.yaml is a half-finished pack that
    discovery silently ignores."""
    stray = [
        d.name
        for d in DOMAINS_ROOT.iterdir()
        if d.is_dir() and not d.name.startswith(".") and not (d / "pack.yaml").is_file()
    ]

    assert stray == [], f"directories under domains/ without pack.yaml: {stray}"
