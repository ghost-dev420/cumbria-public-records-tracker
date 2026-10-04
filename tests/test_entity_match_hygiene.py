import json

from public_records_tracker.analysis import add_signal, ensure_analysis_schema
from public_records_tracker.db import connect
from public_records_tracker.entity_match_hygiene import (
    cleanup_invalid_entity_matching,
    ensure_safe_variant_matches,
    suppress_value_mismatches_with_incomplete_period_coverage,
)
from public_records_tracker.resolution import ensure_resolution_schema, put_review, run_resolution
from public_records_tracker.structured import ensure_structured_schema, upsert_entity


def test_safe_spacing_and_one_edit_variants_auto_resolve(tmp_path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    ensure_resolution_schema(con)

    spacing_left = upsert_entity(
        con,
        entity_type="SUPPLIER",
        name="CENTRAL CONSULTANCYAND TRAINING LTD",
        namespace="payments",
    )
    spacing_right = upsert_entity(
        con,
        entity_type="SUPPLIER",
        name="Central Consultancy and Training Limited",
        namespace="contracts",
    )
    typo_left = upsert_entity(
        con,
        entity_type="SUPPLIER",
        name="COMPASS MINERALS UK LIMITED",
        namespace="payments",
    )
    typo_right = upsert_entity(
        con,
        entity_type="SUPPLIER",
        name="Compass Mineral UK Ltd",
        namespace="contracts",
    )

    assert ensure_safe_variant_matches(con) == 2

    rows = con.execute(
        """SELECT match_method,status,left_entity_id,right_entity_id
           FROM entity_matches
           WHERE status='auto_accepted'"""
    ).fetchall()
    methods = {row[0] for row in rows}
    assert "whitespace_join_normalized" in methods
    assert "conservative_one_edit" in methods

    matched_entities = {item for row in rows for item in row[2:]}
    assert {spacing_left, spacing_right, typo_left, typo_right} <= matched_entities
    con.close()


def test_date_like_entity_matches_are_rejected_and_reviews_resolved(tmp_path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    ensure_resolution_schema(con)

    first = upsert_entity(
        con,
        entity_type="SUPPLIER",
        name="01/09/2025",
        namespace="bad-source",
    )
    second = upsert_entity(
        con,
        entity_type="SUPPLIER",
        name="01/04/2025",
        namespace="bad-source",
    )
    run_resolution(con)

    assert con.execute(
        "SELECT count(*) FROM review_queue WHERE item_type='ENTITY_MATCH' AND status='open'"
    ).fetchone()[0] >= 1

    stats = cleanup_invalid_entity_matching(con)
    assert stats["invalid_reviews_resolved"] >= 1
    assert con.execute(
        """SELECT count(*) FROM review_queue
           WHERE item_type='ENTITY_MATCH' AND status='open'
             AND (subject_entity_id IN (?,?) OR object_entity_id IN (?,?))""",
        [first, second, first, second],
    ).fetchone()[0] == 0
    assert con.execute(
        """SELECT count(*) FROM entity_matches
           WHERE status='rejected'
             AND (left_entity_id IN (?,?) OR right_entity_id IN (?,?))""",
        [first, second, first, second],
    ).fetchone()[0] >= 1
    con.close()


def test_value_signal_is_retired_when_period_coverage_is_incomplete(tmp_path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_analysis_schema(con)
    ensure_resolution_schema(con)

    value_signal = add_signal(
        con,
        signal_type="PAYMENTS_EXCEED_TRACKED_CONTRACT_VALUE",
        score=0.55,
        summary="Payments exceed tracked value",
        subject="supplier-1",
        object_="buyer-1",
        evidence=["payment-1", "contract-value-1"],
        metadata={"interpretation": "review_required_not_a_finding"},
    )
    period_signal = add_signal(
        con,
        signal_type="PAYMENTS_OUTSIDE_TRACKED_CONTRACT_PERIODS",
        score=0.45,
        summary="Payments outside tracked periods",
        subject="supplier-1",
        object_="buyer-1",
        evidence=["payment-2", "period-1"],
    )
    review_id = put_review(
        con,
        item_type="SIGNAL_REVIEW",
        score=0.55,
        summary="Review payment / tracked contract value difference",
        subject="supplier-1",
        object_="buyer-1",
        evidence=["payment-1", "contract-value-1"],
        metadata={"signal_id": value_signal},
    )

    assert suppress_value_mismatches_with_incomplete_period_coverage(con) == 1
    value_status, metadata_json = con.execute(
        "SELECT status,metadata_json FROM signals WHERE signal_id=?", [value_signal]
    ).fetchone()
    assert value_status == "stale"
    metadata = json.loads(metadata_json)
    assert metadata["suppressed_reason"] == "tracked_periods_do_not_cover_payment_stream"
    assert metadata["interpretation"] == "suppressed_incomplete_contract_coverage"
    assert con.execute(
        "SELECT status FROM signals WHERE signal_id=?", [period_signal]
    ).fetchone()[0] == "review"
    assert con.execute(
        "SELECT status FROM review_queue WHERE review_id=?", [review_id]
    ).fetchone()[0] == "stale"
    con.close()
