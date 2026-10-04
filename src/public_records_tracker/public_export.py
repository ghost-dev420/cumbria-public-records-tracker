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
from .public_findings import build_public_findings
from .publication import build_publication_status
from .reference import ensure_reference_schema
from .resolution import ensure_resolution_schema
from .site import build_site as render_site
from .structured import ensure_structured_schema


PUBLIC_MATCH_STATUSES = ("auto_accepted", "accepted")
PUBLIC_SIGNAL_STATUSES = ("approved",)
PUBLIC_CLAIM_STATUSES = ("approved",)

# These signal types describe the state of the tracker's public-record coverage,
# rather than alleging misconduct. They may therefore be published automatically
# when their detector metadata explicitly carries the expected neutral interpretation.
AUTO_PUBLIC_SIGNAL_INTERPRETATIONS = {
    "SUPPLIER_PAYMENT_CONTRACT_OVERLAP": "documented_cross_source_link_not_a_finding",
    "PAYMENT_STREAM_WITHOUT_TRACKED_PROCUREMENT_LINK": "source_discovery_review_not_a_finding",
    "PAYMENTS_OUTSIDE_TRACKED_CONTRACT_PERIODS": "review_required_not_a_finding",
}


def _auto_publish_safe_observations(con) -> int:
    rows = con.execute(
        """SELECT signal_id,signal_type,metadata_json
           FROM signals
           WHERE status='review'"""
    ).fetchall()
    approved: list[tuple[str]] = []
    for signal_id, signal_type, metadata_json in rows:
        expected = AUTO_PUBLIC_SIGNAL_INTERPRETATIONS.get(str(signal_type))
        if expected is None:
            continue
        try:
            metadata = json.loads(metadata_json or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(metadata, dict) or metadata.get("interpretation") != expected:
            continue
        approved.append((str(signal_id),))
    if approved:
        con.executemany(
            "UPDATE signals SET status='approved',updated_at=now() WHERE signal_id=?",
            approved,
        )
    return len(approved)


def prepare_public_db(source_db: Path, target_db: Path) -> dict[str, int]:
    """Copy a working database and remove material that is unsafe for publication.

    Human-review material remains private. A narrow allow-list of neutral coverage
    observations is approved only inside the temporary publication copy, leaving
    the working database and its review workflow unchanged.
    """
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

    auto_published = _auto_publish_safe_observations(con)

    con.execute(
        "DELETE FROM entity_matches WHERE status NOT IN ('auto_accepted','accepted')"
    )
    con.execute("DELETE FROM review_queue")
    con.execute("DELETE FROM signals WHERE status <> 'approved'")

    # Claims are private-by-default just like higher-risk analytical signals. Remove
    # dependent evidence rows first so an unreviewed claim can never surface merely
    # because its underlying source material is public.
    con.execute(
        """DELETE FROM claim_evidence
           WHERE claim_id IN (
             SELECT claim_id FROM claims WHERE status <> 'approved'
           )"""
    )
    con.execute("DELETE FROM claims WHERE status <> 'approved'")

    # Public output keeps cryptographic/source provenance but never needs the
    # collector host's private filesystem layout.
    con.execute("UPDATE snapshots SET archive_path='' ")
    con.execute("UPDATE observations SET observation_path='' ")

    after_matches = con.execute("SELECT count(*) FROM entity_matches").fetchone()[0]
    after_signals = con.execute("SELECT count(*) FROM signals").fetchone()[0]
    con.close()

    return {
        "withheld_entity_matches": int(before_matches - after_matches),
        "withheld_review_items": int(before_reviews),
        "withheld_signals": int(before_signals - after_signals),
        "auto_published_observations": int(auto_published),
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
        endpoints["findings"] = "api/findings.json"
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
    """Render the sanitized evidence site and plain-language accountability dashboard."""
    with tempfile.TemporaryDirectory(prefix="prt-public-") as temp_dir:
        public_db = Path(temp_dir) / "public.duckdb"
        stats = prepare_public_db(db_path, public_db)
        render_site(public_db, out_dir)
        build_publication_status(public_db, out_dir)
        build_structured_changes(public_db, out_dir)
        build_entity_pages(public_db, out_dir)
        stats["evidence_packages"] = build_evidence_packages(public_db, out_dir)
        stats.update(build_public_findings(public_db, out_dir))
    _remove_internal_review_surface(out_dir)
    return stats
