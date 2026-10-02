from public_records_tracker.db import connect
from public_records_tracker.diffs import ensure_diff_schema, record_structured_diff
from public_records_tracker.structured import add_fact, activate_snapshot, ensure_structured_schema


def _fact(con, snapshot_id: str, value: str) -> None:
    add_fact(
        con,
        document_id="doc-1",
        snapshot_id=snapshot_id,
        fact_type="REGISTER_OF_INTEREST_ENTRY",
        predicate="HAS_REGISTER_ENTRY",
        evidence_class="OFFICIAL_RECORD",
        subject_entity_id="person-1",
        value_text=value,
        locator="question 1",
    )


def test_records_added_and_removed_facts_between_snapshots(tmp_path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    ensure_diff_schema(con)

    activate_snapshot(
        con,
        document_id="doc-1",
        snapshot_id="snap-old",
        observed_at="2026-01-01T00:00:00+00:00",
    )
    _fact(con, "snap-old", "Acme Services Ltd")

    activate_snapshot(
        con,
        document_id="doc-1",
        snapshot_id="snap-new",
        observed_at="2026-02-01T00:00:00+00:00",
    )
    _fact(con, "snap-new", "Beta Services Ltd")

    count = record_structured_diff(
        con,
        document_id="doc-1",
        new_snapshot_id="snap-new",
    )
    assert count == 2
    changes = con.execute(
        "SELECT change_type,value_text FROM structured_changes ORDER BY change_type"
    ).fetchall()
    assert changes == [
        ("FACT_ADDED", "Beta Services Ltd"),
        ("FACT_REMOVED", "Acme Services Ltd"),
    ]
    con.close()


def test_unchanged_fact_does_not_create_change(tmp_path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    ensure_diff_schema(con)

    activate_snapshot(
        con,
        document_id="doc-1",
        snapshot_id="snap-old",
        observed_at="2026-01-01T00:00:00+00:00",
    )
    _fact(con, "snap-old", "Acme Services Ltd")
    activate_snapshot(
        con,
        document_id="doc-1",
        snapshot_id="snap-new",
        observed_at="2026-02-01T00:00:00+00:00",
    )
    _fact(con, "snap-new", "Acme Services Ltd")

    assert record_structured_diff(
        con,
        document_id="doc-1",
        new_snapshot_id="snap-new",
    ) == 0
    con.close()
