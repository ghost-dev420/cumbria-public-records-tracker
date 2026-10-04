from __future__ import annotations

from datetime import datetime

import duckdb

from .analysis import ensure_analysis_schema
from .entity_match_hygiene import (
    cleanup_invalid_entity_matching,
    ensure_safe_variant_matches,
    suppress_value_mismatches_with_incomplete_period_coverage,
)
from .resolution import ensure_resolution_schema


def _db_now(con: duckdb.DuckDBPyConnection) -> datetime:
    """Return the database's local TIMESTAMP clock.

    Analysis tables store ``updated_at`` as a timezone-naive TIMESTAMP while
    DuckDB's ``now()``/``current_timestamp`` is timezone-aware.  Casting inside
    DuckDB makes the cycle boundary use the exact same session-timezone basis
    that is used when those columns are updated.  Using Python UTC-naive time
    here caused up to a one-hour stale-retirement delay during UK BST.
    """
    return con.execute("SELECT CAST(current_timestamp AS TIMESTAMP)").fetchone()[0]


def begin_analysis_cycle(con: duckdb.DuckDBPyConnection) -> datetime:
    """Start a recomputation cycle for automated matches/reviews/signals.

    Previously stale automated items are temporarily made eligible for
    rediscovery. Upsert paths refresh updated_at for items still supported by
    the latest facts; finish_analysis_cycle retires the rest again.
    """
    ensure_analysis_schema(con)
    ensure_resolution_schema(con)
    started = _db_now(con)
    con.execute("UPDATE signals SET status='review' WHERE status='stale'")
    con.execute("UPDATE review_queue SET status='open' WHERE status='stale'")
    # Apply only conservative, deterministic spelling/spacing matches before
    # the resolver rebuilds canonical membership for this cycle.
    ensure_safe_variant_matches(con)
    return started


def finish_analysis_cycle(
    con: duckdb.DuckDBPyConnection, started_at: datetime
) -> dict[str, int]:
    """Retire automated review material not reproduced from current facts."""
    hygiene = cleanup_invalid_entity_matching(con)
    suppressed_value_signals = suppress_value_mismatches_with_incomplete_period_coverage(con)

    finished = _db_now(con)
    stale_signals = con.execute(
        """UPDATE signals
           SET status='stale', updated_at=?
           WHERE status IN ('review','approved') AND updated_at < ?
           RETURNING signal_id""",
        [finished, started_at],
    ).fetchall()
    stale_reviews = con.execute(
        """UPDATE review_queue
           SET status='stale', updated_at=?
           WHERE status='open' AND updated_at < ?
           RETURNING review_id""",
        [finished, started_at],
    ).fetchall()
    return {
        "stale_signals": len(stale_signals),
        "stale_reviews": len(stale_reviews),
        "invalid_entity_aliases_removed": hygiene["invalid_aliases_removed"],
        "invalid_entity_matches_rejected": hygiene["invalid_matches_rejected"],
        "invalid_entity_reviews_resolved": hygiene["invalid_reviews_resolved"],
        "value_signals_suppressed_incomplete_coverage": suppressed_value_signals,
    }
