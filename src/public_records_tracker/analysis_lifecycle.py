from __future__ import annotations

from datetime import datetime, timezone

import duckdb

from .analysis import ensure_analysis_schema
from .resolution import ensure_resolution_schema


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def begin_analysis_cycle(con: duckdb.DuckDBPyConnection) -> datetime:
    """Start a recomputation cycle for automated matches/reviews/signals.

    Previously stale automated items are temporarily made eligible for
    rediscovery. Upsert paths refresh updated_at for items still supported by
    the latest facts; finish_analysis_cycle retires the rest again.
    """
    ensure_analysis_schema(con)
    ensure_resolution_schema(con)
    started = _utc_now_naive()
    con.execute("UPDATE signals SET status='review' WHERE status='stale'")
    con.execute("UPDATE review_queue SET status='open' WHERE status='stale'")
    return started


def finish_analysis_cycle(
    con: duckdb.DuckDBPyConnection, started_at: datetime
) -> dict[str, int]:
    """Retire automated review material not reproduced from current facts."""
    stale_signals = con.execute(
        """UPDATE signals
           SET status='stale', updated_at=?
           WHERE status IN ('review','approved') AND updated_at < ?
           RETURNING signal_id""",
        [_utc_now_naive(), started_at],
    ).fetchall()
    stale_reviews = con.execute(
        """UPDATE review_queue
           SET status='stale', updated_at=?
           WHERE status='open' AND updated_at < ?
           RETURNING review_id""",
        [_utc_now_naive(), started_at],
    ).fetchall()
    return {
        "stale_signals": len(stale_signals),
        "stale_reviews": len(stale_reviews),
    }
