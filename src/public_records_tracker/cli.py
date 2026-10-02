from __future__ import annotations

from pathlib import Path

import typer

from .analysis import ensure_analysis_schema, run_detectors
from .db import connect
from .diffs import ensure_diff_schema
from .procurement_gaps import run_procurement_gap_detectors
from .public_export import build_public_site
from .reconciliation import run_reconciliation_detectors
from .reference import ensure_reference_schema, refresh_reference_index
from .resolution import ensure_resolution_schema, run_resolution
from .runner import run_collection
from .structured import ensure_structured_schema

app = typer.Typer(no_args_is_help=True)
DEFAULT_DB = Path("data/tracker.duckdb")
DEFAULT_ARCHIVE = Path("data/raw")
DEFAULT_CONFIG = Path("config/sources.yml")


@app.command("init-db")
def init_db(db: Path = DEFAULT_DB) -> None:
    con = connect(db)
    ensure_structured_schema(con)
    ensure_resolution_schema(con)
    ensure_analysis_schema(con)
    ensure_reference_schema(con)
    ensure_diff_schema(con)
    con.close()
    typer.echo(f"Initialised {db}")


@app.command()
def collect(
    source: str = typer.Option("all", help="Source id or 'all'"),
    page_limit: int | None = typer.Option(None, help="Override listing/API page limit"),
    strict: bool = typer.Option(
        False,
        help="Exit non-zero if any source is blocked or fails. Default preserves partial success.",
    ),
    config: Path = DEFAULT_CONFIG,
    db: Path = DEFAULT_DB,
    archive: Path = DEFAULT_ARCHIVE,
) -> None:
    stats = run_collection(
        config_path=config,
        db_path=db,
        archive_root=archive,
        source_id=source,
        page_limit=page_limit,
    )
    typer.echo(
        "Collected "
        f"{stats['records']} records / {stats['facts']} facts from {stats['sources']} sources; "
        f"structured_changes={stats['structured_changes']} "
        f"source_errors={stats['errors']} blocked={stats['blocked']} "
        f"extraction_errors={stats['extraction_errors']} matches={stats['matches']} "
        f"open_reviews={stats['review_items']} signals={stats['signals']}"
    )
    if strict and (stats["errors"] or stats["extraction_errors"]):
        raise typer.Exit(code=2)


@app.command("resolve-entities")
def resolve_entities(db: Path = DEFAULT_DB) -> None:
    con = connect(db)
    ensure_structured_schema(con)
    ensure_resolution_schema(con)
    stats = run_resolution(con)
    open_reviews = con.execute(
        "SELECT count(*) FROM review_queue WHERE status='open'"
    ).fetchone()[0]
    con.close()
    typer.echo(f"Resolution pass: {stats}; open_reviews={open_reviews}")


@app.command("detect-signals")
def detect_signals(db: Path = DEFAULT_DB) -> None:
    con = connect(db)
    ensure_structured_schema(con)
    ensure_resolution_schema(con)
    ensure_analysis_schema(con)
    stats = run_detectors(con)
    stats.update(run_reconciliation_detectors(con))
    stats.update(run_procurement_gap_detectors(con))
    con.close()
    typer.echo(f"Signal pass: {stats}")


@app.command("approve-signal")
def approve_signal(signal_id: str, db: Path = DEFAULT_DB) -> None:
    """Mark one reviewed signal as eligible for public publication."""
    con = connect(db)
    ensure_analysis_schema(con)
    exists = con.execute("SELECT 1 FROM signals WHERE signal_id=?", [signal_id]).fetchone()
    if not exists:
        con.close()
        raise typer.BadParameter(f"Unknown signal_id: {signal_id}")
    con.execute(
        "UPDATE signals SET status='approved', updated_at=now() WHERE signal_id=?",
        [signal_id],
    )
    con.close()
    typer.echo(f"Approved signal {signal_id} for publication")


@app.command("withhold-signal")
def withhold_signal(signal_id: str, db: Path = DEFAULT_DB) -> None:
    """Return a signal to internal review so it is withheld from public output."""
    con = connect(db)
    ensure_analysis_schema(con)
    exists = con.execute("SELECT 1 FROM signals WHERE signal_id=?", [signal_id]).fetchone()
    if not exists:
        con.close()
        raise typer.BadParameter(f"Unknown signal_id: {signal_id}")
    con.execute(
        "UPDATE signals SET status='review', updated_at=now() WHERE signal_id=?",
        [signal_id],
    )
    con.close()
    typer.echo(f"Withheld signal {signal_id} from publication")


@app.command("refresh-reference-index")
def refresh_registry_reference_index(
    db: Path = DEFAULT_DB,
    cache_dir: Path = Path("data/reference-cache"),
    psc: bool = typer.Option(
        False,
        "--psc/--no-psc",
        help="Also scan the Companies House PSC bulk snapshot. This is much larger.",
    ),
) -> None:
    con = connect(db)
    ensure_structured_schema(con)
    ensure_resolution_schema(con)
    ensure_reference_schema(con)
    stats = refresh_reference_index(con, cache_dir=cache_dir, include_psc=psc)
    con.close()
    typer.echo(f"Reference-index refresh: {stats}")


@app.command("build-site")
def build_site(db: Path = DEFAULT_DB, out: Path = Path("site")) -> None:
    stats = build_public_site(db, out)
    typer.echo(
        f"Built publication-gated site at {out / 'index.html'}; "
        f"published_signals={stats['published_signals']} "
        f"withheld_signals={stats['withheld_signals']} "
        f"withheld_review_items={stats['withheld_review_items']} "
        f"withheld_entity_matches={stats['withheld_entity_matches']}"
    )


if __name__ == "__main__":
    app()
