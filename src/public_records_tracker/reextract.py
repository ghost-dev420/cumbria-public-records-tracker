from __future__ import annotations

import json
from pathlib import Path

from .analysis import ensure_analysis_schema, run_detectors
from .config import load_organisations, load_sources
from .db import (
    connect,
    link_source_organisations,
    register_organisation,
    register_source,
)
from .extractors import EXTRACTORS
from .models import EvidenceClass, Record
from .procurement_gaps import run_procurement_gap_detectors
from .reconciliation import run_reconciliation_detectors
from .resolution import ensure_resolution_schema, run_resolution
from .structured import ensure_structured_schema


def reextract_latest_archive(
    *,
    config_path: Path,
    db_path: Path,
    source_id: str = "all",
) -> dict[str, int]:
    """Re-run current extractors against latest archived document bytes without network access."""
    sources = load_sources(config_path)
    source_map = {source["id"]: source for source in sources if source.get("enabled", True)}
    if source_id != "all" and source_id not in source_map:
        raise ValueError(f"Unknown or disabled source: {source_id}")

    selected_ids = set(source_map) if source_id == "all" else {source_id}
    con = connect(db_path)
    ensure_structured_schema(con)
    ensure_resolution_schema(con)
    ensure_analysis_schema(con)

    for organisation in load_organisations(config_path.with_name("organisations.yml")):
        register_organisation(con, organisation)
    for selected_id in selected_ids:
        source = source_map[selected_id]
        register_source(con, source)
        link_source_organisations(con, source)

    rows = con.execute(
        """SELECT d.document_id,d.source_id,d.canonical_url,d.title,d.published_at,
                  d.evidence_class,d.metadata_json,d.latest_snapshot_id,
                  s.archive_path,s.content_type,s.status_code,s.etag,s.last_modified
           FROM documents d
           JOIN snapshots s ON s.snapshot_id=d.latest_snapshot_id
           WHERE d.source_id IN (SELECT unnest(?))
           ORDER BY d.source_id,d.document_id""",
        [sorted(selected_ids)],
    ).fetchall()

    stats = {
        "documents": 0,
        "facts": 0,
        "skipped_missing_blob": 0,
        "extraction_errors": 0,
        "matches": 0,
        "review_items": 0,
        "signals": 0,
    }
    for row in rows:
        (
            document_id,
            row_source_id,
            canonical_url,
            title,
            published_at,
            evidence_class,
            metadata_json,
            snapshot_id,
            archive_path,
            content_type,
            status_code,
            etag,
            last_modified,
        ) = row
        source = source_map[str(row_source_id)]
        extractor_names = list(source.get("extractors", []))
        if source.get("kind") in {"contracts_finder", "find_tender"} and "contracts_finder" not in extractor_names:
            extractor_names.append("contracts_finder")
        if not extractor_names:
            continue

        blob = Path(str(archive_path))
        if not blob.exists():
            stats["skipped_missing_blob"] += 1
            continue
        try:
            metadata = json.loads(metadata_json or "{}")
        except json.JSONDecodeError:
            metadata = {}
        record = Record(
            source_id=str(row_source_id),
            url=str(canonical_url),
            title=str(title),
            body=blob.read_bytes(),
            content_type=str(content_type or "application/octet-stream"),
            evidence_class=EvidenceClass(str(evidence_class)),
            published_at=str(published_at) if published_at is not None else None,
            metadata=metadata,
            status_code=int(status_code or 200),
            etag=str(etag) if etag is not None else None,
            last_modified=str(last_modified) if last_modified is not None else None,
        )

        # Rebuild facts for this exact snapshot from the complete configured extractor set.
        con.execute("DELETE FROM facts WHERE document_id=? AND snapshot_id=?", [document_id, snapshot_id])
        record_failed = False
        added = 0
        for extractor_name in extractor_names:
            extractor = EXTRACTORS.get(extractor_name)
            if extractor is None:
                stats["extraction_errors"] += 1
                record_failed = True
                continue
            try:
                added += extractor(
                    con=con,
                    record=record,
                    document_id=str(document_id),
                    snapshot_id=str(snapshot_id),
                    source=source,
                )
            except Exception:
                stats["extraction_errors"] += 1
                record_failed = True
        if record_failed:
            # Avoid publishing a partially reconstructed snapshot.
            con.execute("DELETE FROM facts WHERE document_id=? AND snapshot_id=?", [document_id, snapshot_id])
            continue
        stats["documents"] += 1
        stats["facts"] += added

    resolution_stats = run_resolution(con)
    detector_stats = run_detectors(con)
    detector_stats.update(run_reconciliation_detectors(con))
    detector_stats.update(run_procurement_gap_detectors(con))
    stats["matches"] = sum(
        resolution_stats.get(key, 0)
        for key in ("identifier", "name_exact", "fuzzy_review")
    )
    stats["review_items"] = con.execute(
        "SELECT count(*) FROM review_queue WHERE status='open'"
    ).fetchone()[0]
    stats["signals"] = sum(detector_stats.values())
    con.close()
    return stats
