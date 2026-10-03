from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

from .analysis import ensure_analysis_schema
from .db import connect
from .diffs import build_structured_changes, ensure_diff_schema
from .entity_pages import build_entity_pages
from .evidence_packages import build_evidence_packages
from .publication import build_publication_status
from .reference import ensure_reference_schema
from .resolution import ensure_resolution_schema
from .site import build_site as render_site
from .structured import ensure_structured_schema


PUBLIC_MATCH_STATUSES = ("auto_accepted", "accepted")
PUBLIC_SIGNAL_STATUSES = ("approved",)
PUBLIC_REDACTED_NARRATIVE_FACT_TYPES = (
    "HOUSING_OMBUDSMAN_DETERMINATION",
    "OMBUDSMAN_SUMMARY",
)
PUBLIC_NARRATIVE_REDACTION = (
    "[Narrative withheld from structured public output; follow the official source and locator.]"
)


def prepare_public_db(source_db: Path, target_db: Path) -> dict[str, int]:
    """Copy a working database and remove material that has not passed publication review."""
    target_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_db, target_db)

    con = connect(target_db)
    ensure_structured_schema(con)
    ensure_resolution_schema(con)
    ensure_analysis_schema(con)
    ensure_reference_schema(con)
    ensure_diff_schema(con)

    before_matches = con.execute("SELECT count(*) FROM entity_matches").fetchone()[0]
    before_reviews = con.execute("SELECT count(*) FROM review_queue").fetchone()[0]
    before_signals = con.execute("SELECT count(*) FROM signals").fetchone()[0]

    con.execute(
        "DELETE FROM entity_matches WHERE status NOT IN ('auto_accepted','accepted')"
    )
    con.execute("DELETE FROM review_queue")
    con.execute("DELETE FROM signals WHERE status <> 'approved'")

    # Keep categorical regulatory outcomes and exact provenance public, but do
    # not republish free-text case narratives that can contain unnecessary
    # health, family or other personal detail even when the official decision
    # itself is public and anonymised. The working DB retains the original text.
    placeholders = ",".join("?" for _ in PUBLIC_REDACTED_NARRATIVE_FACT_TYPES)
    params = [PUBLIC_NARRATIVE_REDACTION, *PUBLIC_REDACTED_NARRATIVE_FACT_TYPES]
    con.execute(
        f"UPDATE facts SET value_text=? WHERE fact_type IN ({placeholders})",
        params,
    )
    con.execute(
        f"UPDATE structured_changes SET value_text=? WHERE fact_type IN ({placeholders})",
        params,
    )

    # Public output keeps cryptographic/source provenance but never needs the
    # collector host's private filesystem layout. Blank these paths on the
    # temporary publication copy before any JSON, HTML or evidence manifests
    # are rendered.
    con.execute("UPDATE snapshots SET archive_path='' ")
    con.execute("UPDATE observations SET observation_path='' ")

    after_matches = con.execute("SELECT count(*) FROM entity_matches").fetchone()[0]
    after_signals = con.execute("SELECT count(*) FROM signals").fetchone()[0]
    con.close()

    return {
        "withheld_entity_matches": int(before_matches - after_matches),
        "withheld_review_items": int(before_reviews),
        "withheld_signals": int(before_signals - after_signals),
        "published_entity_matches": int(after_matches),
        "published_signals": int(after_signals),
    }


def _remove_internal_review_surface(out_dir: Path) -> None:
    review_json = out_dir / "api" / "review-queue.json"
    review_json.unlink(missing_ok=True)

    api_index = out_dir / "api" / "index.json"
    if api_index.exists():
        payload = json.loads(api_index.read_text(encoding="utf-8"))
        endpoints = payload.get("endpoints") or {}
        endpoints.pop("review-queue", None)
        endpoints["evidence-packages"] = "api/evidence-packages.json"
        endpoints["publication-status"] = "api/publication-status.json"
        endpoints["structured-changes"] = "api/structured-changes.json"
        endpoints["entities"] = "api/entities.json"
        payload["endpoints"] = endpoints
        api_index.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    index_path = out_dir / "index.html"
    if not index_path.exists():
        return
    page = index_path.read_text(encoding="utf-8")
    page = re.sub(
        r"<section><h2>Human review queue</h2>.*?</section>",
        "",
        page,
        count=1,
        flags=re.DOTALL,
    )
    page = re.sub(
        r'<div class="card"><div class="n">\d+</div><div>open_reviews</div></div>',
        "",
        page,
        count=1,
    )
    if "href='entities.html'" not in page:
        page = page.replace(
            "<div class='nav'>",
            "<div class='nav'><a class='button' href='entities.html'>Entities & timelines</a>",
            1,
        )
    index_path.write_text(page, encoding="utf-8")


def build_public_site(db_path: Path, out_dir: Path) -> dict[str, int]:
    """Render only publication-approved analysis from a working tracker database."""
    with tempfile.TemporaryDirectory(prefix="prt-public-") as temp_dir:
        public_db = Path(temp_dir) / "public.duckdb"
        stats = prepare_public_db(db_path, public_db)
        render_site(public_db, out_dir)
        build_publication_status(public_db, out_dir)
        build_structured_changes(public_db, out_dir)
        build_entity_pages(public_db, out_dir)
        stats["evidence_packages"] = build_evidence_packages(public_db, out_dir)
    _remove_internal_review_surface(out_dir)
    return stats
