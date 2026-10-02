from __future__ import annotations

from pathlib import Path

from .analysis import ensure_analysis_schema, run_detectors
from .archive import archive_record
from .collectors import COLLECTORS
from .config import load_organisations, load_sources
from .db import (
    connect,
    ingest,
    link_source_organisations,
    register_organisation,
    register_source,
)
from .diffs import ensure_diff_schema, record_structured_diff
from .extractors import EXTRACTORS
from .health import (
    classify_source_error,
    ensure_health_schema,
    finish_source_run,
    start_source_run,
)
from .http import SafeHttpClient
from .procurement_gaps import run_procurement_gap_detectors
from .reconciliation import run_reconciliation_detectors
from .resolution import ensure_resolution_schema, run_resolution
from .structured import activate_snapshot, ensure_structured_schema


def run_collection(
    *,
    config_path: Path,
    db_path: Path,
    archive_root: Path,
    source_id: str = "all",
    page_limit: int | None = None,
) -> dict[str, int]:
    sources = load_sources(config_path)
    organisations = load_organisations(config_path.with_name("organisations.yml"))
    selected = [
        source
        for source in sources
        if source.get("enabled", True) and (source_id == "all" or source["id"] == source_id)
    ]
    if source_id != "all" and not selected:
        raise ValueError(f"Unknown or disabled source: {source_id}")

    con = connect(db_path)
    ensure_structured_schema(con)
    ensure_resolution_schema(con)
    ensure_analysis_schema(con)
    ensure_health_schema(con)
    ensure_diff_schema(con)
    for organisation in organisations:
        register_organisation(con, organisation)

    stats = {
        "sources": 0,
        "records": 0,
        "facts": 0,
        "structured_changes": 0,
        "errors": 0,
        "blocked": 0,
        "extraction_errors": 0,
        "matches": 0,
        "review_items": 0,
        "signals": 0,
    }
    with SafeHttpClient() as client:
        for source in selected:
            register_source(con, source)
            link_source_organisations(con, source)
            stats["sources"] += 1
            run_id = start_source_run(con, source["id"])
            source_records = 0
            source_facts = 0
            source_extraction_errors = 0
            cls = COLLECTORS.get(source["kind"])
            if cls is None:
                error = ValueError(f"unsupported collector kind: {source['kind']}")
                print(f"WARN {error}")
                stats["errors"] += 1
                finish_source_run(
                    con,
                    run_id=run_id,
                    status="configuration_error",
                    record_count=0,
                    fact_count=0,
                    error=error,
                )
                continue
            print(f"Collecting {source['id']}...")
            collector = cls(source, client, page_limit=page_limit)
            source_error: Exception | None = None
            try:
                for record in collector.collect():
                    snap = archive_record(record, archive_root)
                    document_id = ingest(con, record, snap)
                    activate_snapshot(
                        con,
                        document_id=document_id,
                        snapshot_id=snap.snapshot_id,
                        observed_at=snap.retrieved_at,
                    )
                    stats["records"] += 1
                    source_records += 1
                    extractor_names = list(source.get("extractors", []))
                    if (
                        source.get("kind") == "contracts_finder"
                        and "contracts_finder" not in extractor_names
                    ):
                        extractor_names.append("contracts_finder")
                    record_extraction_failed = False
                    for extractor_name in extractor_names:
                        extractor = EXTRACTORS.get(extractor_name)
                        if extractor is None:
                            print(f"WARN unsupported extractor: {extractor_name}")
                            stats["extraction_errors"] += 1
                            source_extraction_errors += 1
                            record_extraction_failed = True
                            continue
                        try:
                            added = extractor(
                                con=con,
                                record=record,
                                document_id=document_id,
                                snapshot_id=snap.snapshot_id,
                                source=source,
                            )
                            stats["facts"] += added
                            source_facts += added
                        except Exception as exc:
                            print(
                                f"ERROR extractor {extractor_name} "
                                f"for {record.url}: {exc}"
                            )
                            stats["extraction_errors"] += 1
                            source_extraction_errors += 1
                            record_extraction_failed = True
                    if not record_extraction_failed:
                        stats["structured_changes"] += record_structured_diff(
                            con,
                            document_id=document_id,
                            new_snapshot_id=snap.snapshot_id,
                        )
            except Exception as exc:
                source_error = exc
                status = classify_source_error(exc)
                print(f"ERROR {source['id']} [{status}]: {exc}")
                stats["errors"] += 1
                if status == "blocked":
                    stats["blocked"] += 1
                finish_source_run(
                    con,
                    run_id=run_id,
                    status=status,
                    record_count=source_records,
                    fact_count=source_facts,
                    error=exc,
                )
            if source_error is None:
                collector_errors = list(getattr(collector, "errors", []))
                if collector_errors:
                    first = collector_errors[0]
                    status = "partial_blocked" if any(
                        classify_source_error(err) == "blocked" for err in collector_errors
                    ) else "partial"
                    stats["errors"] += len(collector_errors)
                    stats["blocked"] += sum(
                        classify_source_error(err) == "blocked" for err in collector_errors
                    )
                    finish_source_run(
                        con,
                        run_id=run_id,
                        status=status,
                        record_count=source_records,
                        fact_count=source_facts,
                        error=first,
                    )
                else:
                    status = "partial" if source_extraction_errors else "success"
                    finish_source_run(
                        con,
                        run_id=run_id,
                        status=status,
                        record_count=source_records,
                        fact_count=source_facts,
                    )

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
