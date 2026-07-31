"""Command line entry point.

    graphpack packs list                  known packs
    graphpack packs validate [PACK]       static checks, no services needed
    graphpack packs schema PACK           compiled extraction schema
    graphpack migrate [--dry-run]         apply pending migrations
    graphpack migrate status              what is applied, what is pending
    graphpack pack reset PACK             pack-scoped teardown

Always run from the GraphPack repository root: the engine's ``Settings`` reads
``.env`` relative to the working directory, and picking up the engine's own
configuration is a mistake that only shows up as data in the wrong place.
"""

from __future__ import annotations

import logging
import sys

import typer
from rich.console import Console
from rich.table import Table

from graphpack import __version__
from graphpack.packs import PackError, list_packs, load_pack
from graphpack.packs.ontology import OntologyError, compile_ontology

app = typer.Typer(
    name="graphpack",
    help="Declarative domain packs on top of the flexible-graphrag engine.",
    no_args_is_help=True,
    add_completion=False,
)
packs_app = typer.Typer(help="Inspect and validate packs.", no_args_is_help=True)
pack_app = typer.Typer(help="Operate on a single pack.", no_args_is_help=True)
backbone_app = typer.Typer(
    help="Structured data into the graph — no LLM involved.", no_args_is_help=True
)
app.add_typer(packs_app, name="packs")
app.add_typer(pack_app, name="pack")
app.add_typer(backbone_app, name="backbone")

console = Console()
err_console = Console(stderr=True)


@app.callback()
def _root(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show debug logging."),
) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # The engine logs an essay at INFO on import; keep our own output readable.
    if not verbose:
        for noisy in ("httpx", "neo4j", "llama_index", "urllib3"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
    # Neo4j reports "unknown property key" for every query against a property
    # that does not exist yet. On a fresh database that is every query we make,
    # and it says nothing about correctness.
    logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)


@app.command()
def version() -> None:
    """Print the GraphPack version."""
    console.print(f"graphpack {__version__}")


# ----------------------------------------------------------------------
# packs
# ----------------------------------------------------------------------


@packs_app.command("list")
def packs_list() -> None:
    """List every pack under domains/."""
    names = list_packs()
    if not names:
        console.print("[yellow]No packs found.[/yellow]")
        return
    table = Table("pack", "version", "lang", "qdrant collection")
    for name in names:
        pack = load_pack(name)
        table.add_row(pack.name, pack.version, pack.lang, pack.qdrant_collection)
    console.print(table)


@packs_app.command("validate")
def packs_validate(
    pack: str | None = typer.Argument(None, help="Pack to validate; omit for all."),
) -> None:
    """Run static checks over one pack or all of them."""
    from graphpack.packs.validate import validate_all, validate_pack

    results = [validate_pack(pack)] if pack else validate_all()
    if not results:
        console.print("[yellow]No packs to validate.[/yellow]")
        return

    failed = False
    for result in results:
        if result.ok:
            console.print(f"[green]OK[/green]   {result.pack_name} — {result.summary}")
        else:
            failed = True
            console.print(f"[red]FAIL[/red] {result.pack_name}")
            for error in result.errors:
                console.print(f"       [red]error[/red] {error}")
        for warning in result.warnings:
            console.print(f"       [yellow]note[/yellow]  {warning}")

    if failed:
        raise typer.Exit(code=1)


@packs_app.command("schema")
def packs_schema(
    pack: str = typer.Argument(..., help="Pack whose ontology to compile."),
    as_json: bool = typer.Option(False, "--json", help="Emit the engine schema dict."),
) -> None:
    """Compile a pack's ontology into the engine's extraction schema."""
    loaded = load_pack(pack)
    schema = compile_ontology(loaded.ontology_path)

    if as_json:
        import json

        console.print_json(json.dumps(schema.as_engine_schema()))
        return

    console.print(f"[bold]{loaded.name}[/bold] v{loaded.version} — {schema.summary()}")
    console.print()
    console.print("[bold]entities[/bold]")
    for name in schema.entities:
        props = schema.properties.get(name, {})
        suffix = f"  ({', '.join(sorted(props))})" if props else ""
        console.print(f"  {name}{suffix}")
    console.print()
    console.print("[bold]relations[/bold]")
    constraints = {rel: (s, o) for s, rel, o in schema.triple_constraints}
    for name in schema.relations:
        pair = constraints.get(name)
        suffix = f"  {pair[0]} -> {pair[1]}" if pair else ""
        console.print(f"  {name}{suffix}")
    console.print()
    console.print(
        "[dim]Triple constraints are enforced by GraphPack, not by the engine: "
        "SchemaManager passes only entity and relation lists to the extractor.[/dim]"
    )


# ----------------------------------------------------------------------
# migrate
# ----------------------------------------------------------------------

migrate_app = typer.Typer(help="Apply and inspect migrations.", invoke_without_command=True)


@migrate_app.callback(invoke_without_command=True)
def migrate(
    ctx: typer.Context,
    dry_run: bool = typer.Option(False, "--dry-run", help="List pending migrations only."),
    check: bool = typer.Option(
        False,
        "--check",
        help="Apply nothing; exit non-zero if any migration is pending.",
    ),
) -> None:
    """Apply every pending migration in order.

    ``--check`` exists so CI can assert "nothing pending" through an exit code
    rather than by matching words in this output.
    """
    if ctx.invoked_subcommand is not None:
        return

    from graphpack.backbone import session_scope
    from graphpack.migrations import apply_pending

    with session_scope() as session:
        applied = apply_pending(session, dry_run=dry_run or check)

    if not applied:
        console.print("[green]Up to date[/green] — no pending migrations.")
        return

    if check:
        console.print(f"[red]{len(applied)} migration(s) pending:[/red]")
        for migration in applied:
            console.print(f"  {migration.id}")
        raise typer.Exit(code=1)
    if dry_run:
        console.print(f"[yellow]{len(applied)} pending:[/yellow]")
        for migration in applied:
            console.print(f"  {migration.id}")
    else:
        console.print(f"[green]Applied {len(applied)} migration(s).[/green]")


@migrate_app.command("status")
def migrate_status() -> None:
    """Show applied and pending migrations."""
    from graphpack.backbone import session_scope
    from graphpack.migrations import status

    with session_scope() as session:
        done, pending = status(session)

    table = Table("migration", "scope", "state")
    for migration in done:
        table.add_row(migration.id, migration.scope, "[green]applied[/green]")
    for migration in pending:
        table.add_row(migration.id, migration.scope, "[yellow]pending[/yellow]")
    if not done and not pending:
        console.print("[yellow]No migrations defined.[/yellow]")
        return
    console.print(table)


app.add_typer(migrate_app, name="migrate")


# ----------------------------------------------------------------------
# pack
# ----------------------------------------------------------------------


@pack_app.command("reset")
def pack_reset(
    pack: str = typer.Argument(..., help="Pack to wipe."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation."),
    keep_vectors: bool = typer.Option(
        False, "--keep-vectors", help="Leave the Qdrant collection in place."
    ),
) -> None:
    """Delete a pack's nodes, registry row, migrations and vectors.

    Scoped to one pack: other packs in the shared database are untouched.
    """
    from graphpack.backbone import session_scope
    from graphpack.reset import reset_pack

    loaded = load_pack(pack)
    if not yes:
        typer.confirm(
            f"Delete all graph data for pack '{loaded.name}'"
            + ("" if keep_vectors else f" and drop Qdrant collection '{loaded.qdrant_collection}'")
            + "?",
            abort=True,
        )

    with session_scope() as session:
        outcome = reset_pack(session, loaded, drop_vectors=not keep_vectors)

    console.print(
        f"[green]Reset {loaded.name}[/green] — {outcome['nodes_deleted']} nodes deleted, "
        f"{outcome['migrations_forgotten']} migration(s) forgotten, "
        f"qdrant collection {'dropped' if outcome['qdrant_dropped'] else 'absent'}."
    )


@pack_app.command("register")
def pack_register(pack: str = typer.Argument(..., help="Pack to record in the graph.")) -> None:
    """Write the pack's registry row (name, version, ontology checksum)."""
    from graphpack.backbone import session_scope
    from graphpack.packs import registry

    loaded = load_pack(pack)
    with session_scope() as session:
        drift = registry.ontology_drift(session, loaded)
        registry.register(session, loaded)

    if drift:
        console.print(
            f"[yellow]Ontology changed[/yellow] since the last ingest of '{loaded.name}' "
            f"(was {drift[:12]}…, now {loaded.ontology_checksum[:12]}…). "
            "Existing entities were extracted under the old schema."
        )
    console.print(f"[green]Registered[/green] {loaded.name} v{loaded.version}")


# ----------------------------------------------------------------------
# backbone
# ----------------------------------------------------------------------


@backbone_app.command("fetch")
def backbone_fetch(
    pack: str = typer.Argument(..., help="Pack whose sources to acquire."),
    force: bool = typer.Option(False, "--force", help="Refetch files that already exist."),
) -> None:
    """Download the pack's raw records into domains/<pack>/data/.

    Acquisition is the slow, externally-visible part of the pipeline, so an
    existing file is left alone unless --force says otherwise.
    """
    from graphpack.backbone import fetch_all, load_sources

    loaded = load_pack(pack)
    sources = load_sources(loaded.path("sources.yaml"))
    if not sources.fetch:
        console.print(f"[yellow]{loaded.name} declares no fetch steps.[/yellow]")
        return

    results = fetch_all(sources, loaded.data_dir, force=force)
    for result in results:
        console.print(f"  {result}")
    console.print(f"[green]Fetched {len(results)} source(s)[/green] into {loaded.data_dir}")


@backbone_app.command("load")
def backbone_load(
    pack: str = typer.Argument(..., help="Pack to load into the graph."),
) -> None:
    """Merge the pack's structured records into Neo4j.

    Idempotent: a second run writes the same rows and creates nothing.
    """
    from graphpack.backbone import load_backbone, load_sources, session_scope
    from graphpack.packs import registry

    loaded = load_pack(pack)
    sources = load_sources(loaded.path("sources.yaml"))

    with session_scope() as session:
        report = load_backbone(session, loaded.name, sources, loaded.data_dir)
        registry.register(session, loaded)

    for line in report.lines():
        console.print(f"  {line}")
    console.print(
        f"[green]Loaded {report.total_written:,} rows[/green] for {loaded.name} "
        f"({report.total_created:,} new)"
    )


@backbone_app.command("check")
def backbone_check(
    pack: str = typer.Argument(..., help="Pack whose sanity queries to run."),
) -> None:
    """Run the pack's checks.cypher.

    Queries titled '[must be empty]' are assertions; anything they return fails
    the command. The rest are printed to be read.
    """
    from graphpack.backbone import session_scope
    from graphpack.backbone.checks import run_checks

    loaded = load_pack(pack)
    with session_scope() as session:
        results = run_checks(session, loaded.path("checks.cypher"))

    failed = 0
    for result in results:
        if result.failed:
            failed += 1
            console.print(f"[red]FAIL[/red] {result.check.title} — expected no rows")
        elif result.check.must_be_empty:
            console.print(f"[green]OK[/green]   {result.check.title}")
            continue
        else:
            console.print(f"\n[bold]{result.check.title}[/bold]")

        if not result.rows:
            console.print("  [dim](no rows)[/dim]")
            continue
        table = Table(*result.rows[0].keys())
        for row in result.rows[:25]:
            table.add_row(*("" if v is None else str(v) for v in row.values()))
        console.print(table)
        if len(result.rows) > 25:
            console.print(f"  [dim]… and {len(result.rows) - 25} more rows[/dim]")

    if failed:
        console.print(f"\n[red]{failed} assertion(s) failed.[/red]")
        raise typer.Exit(code=1)


def main() -> None:
    """Turn the expected failures into one-line messages.

    A missing pack, an unparseable ontology or a stopped database are ordinary
    outcomes of running a command, not defects — a traceback for those buries
    the sentence that says what to do.
    """
    from graphpack.backbone import FetchError, LoadError, SourcesError
    from graphpack.backbone.checks import CheckError
    from graphpack.backbone.normalize import NormalizeError
    from graphpack.migrations import MigrationError

    try:
        app()
    except (
        CheckError,
        FetchError,
        LoadError,
        MigrationError,
        NormalizeError,
        OntologyError,
        PackError,
        SourcesError,
    ) as exc:
        err_console.print(f"[red]error[/red] {exc}")
        raise SystemExit(1) from exc
    except ConnectionError as exc:
        err_console.print(f"[red]error[/red] {exc}")
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
