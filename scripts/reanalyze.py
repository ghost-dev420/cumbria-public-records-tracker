#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path

from public_records_tracker.analysis import ensure_analysis_schema, run_detectors
from public_records_tracker.analysis_lifecycle import begin_analysis_cycle, finish_analysis_cycle
from public_records_tracker.analysis_quality import prepare_analysis_resolution
from public_records_tracker.config import load_sources
from public_records_tracker.connections import detect_psc_supplier_connections
from public_records_tracker.db import connect
from public_records_tracker.payment_payer_hygiene import repair_payment_payer_attribution
from public_records_tracker.procurement_gaps import run_procurement_gap_detectors
from public_records_tracker.reconciliation import run_reconciliation_detectors
from public_records_tracker.resolution import ensure_resolution_schema, run_resolution
from public_records_tracker.structured import ensure_structured_schema


def _backup_path(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return path.with_name(f"{path.name}.pre-analysis-{stamp}.bak")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recompute private entity resolution, review items and signals without crawling."
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/tracker.duckdb"),
        help="working DuckDB path (default: data/tracker.duckdb)",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="skip the automatic pre-analysis database backup",
    )
    args = parser.parse_args()

    if not args.db.exists():
        raise SystemExit(f"Database does not exist: {args.db}")

    if not args.no_backup:
        backup = _backup_path(args.db)
        shutil.copy2(args.db, backup)
        print(f"Backup: {backup}")

    con = connect(args.db)
    try:
        ensure_structured_schema(con)
        ensure_resolution_schema(con)
        ensure_analysis_schema(con)

        source_path = Path(__file__).resolve().parents[1] / "config" / "sources.yml"
        payer_hygiene = {"documents_matched": 0, "facts_repaired": 0}
        for source in load_sources(source_path):
            if not source.get("payment_payer_rules"):
                continue
            repaired = repair_payment_payer_attribution(con, source)
            payer_hygiene["documents_matched"] += repaired["documents_matched"]
            payer_hygiene["facts_repaired"] += repaired["facts_repaired"]

        started = begin_analysis_cycle(con)

        resolution = run_resolution(con)
        quality = prepare_analysis_resolution(con)
        detectors = run_detectors(con)
        detectors.update(run_reconciliation_detectors(con))
        detectors.update(run_procurement_gap_detectors(con))
        detectors.update(detect_psc_supplier_connections(con))
        lifecycle = finish_analysis_cycle(con, started)

        open_reviews = con.execute(
            "SELECT count(*) FROM review_queue WHERE status='open'"
        ).fetchone()[0]
        active_signals = con.execute(
            "SELECT count(*) FROM signals WHERE status IN ('review','approved')"
        ).fetchone()[0]

        print("Payment payer hygiene:", payer_hygiene)
        print("Resolution:", resolution)
        print("Quality resolution:", quality)
        print("Detectors:", detectors)
        print("Lifecycle:", lifecycle)
        print(f"Open reviews: {open_reviews}")
        print(f"Active signals: {active_signals}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
