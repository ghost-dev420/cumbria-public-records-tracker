#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from public_records_tracker.analysis import ensure_analysis_schema, run_detectors
from public_records_tracker.analysis_lifecycle import begin_analysis_cycle, finish_analysis_cycle
from public_records_tracker.analysis_quality import prepare_analysis_resolution
from public_records_tracker.db import connect
from public_records_tracker.procurement_gaps import run_procurement_gap_detectors
from public_records_tracker.reconciliation import run_reconciliation_detectors
from public_records_tracker.resolution import ensure_resolution_schema, run_resolution
from public_records_tracker.structured import ensure_structured_schema


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
    args = parser.parse_args()

    con = connect(args.db)
    try:
        ensure_structured_schema(con)
        ensure_resolution_schema(con)
        ensure_analysis_schema(con)
        started = begin_analysis_cycle(con)

        resolution = run_resolution(con)
        quality = prepare_analysis_resolution(con)
        detectors = run_detectors(con)
        detectors.update(run_reconciliation_detectors(con))
        detectors.update(run_procurement_gap_detectors(con))
        lifecycle = finish_analysis_cycle(con, started)

        open_reviews = con.execute(
            "SELECT count(*) FROM review_queue WHERE status='open'"
        ).fetchone()[0]
        active_signals = con.execute(
            "SELECT count(*) FROM signals WHERE status IN ('review','approved')"
        ).fetchone()[0]

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
