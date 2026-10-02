from __future__ import annotations

from pathlib import Path

import typer

from .analysis import ensure_analysis_schema, run_detectors
from .db import connect
from .diffs import build_structured_changes, ensure_diff_schema
from .publication import build_publication_status
from .reference import ensure_reference_schema, refresh_reference_index
from .resolution import ensure_resolution_schema, run_resolution
from .runner import run_collection
from .site import build_site as render_site
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
    con.close()
    typer.echo(f"Signal pass: {stats}")


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
    render_site(db, out)
    build_publication_status(db, out)
    build_structured_changes(db, out)
    typer.echo(
        f"Built {out / 'index.html'}, {out / 'publication-status.html'} "
        f"and {out / 'structured-changes.html'}"
    )


if __name__ == "__main__":
    app()
