from pathlib import Path

from public_records_tracker.db import connect
from public_records_tracker.resolution import (
    add_identifier,
    ensure_resolution_schema,
    normalize_org_name,
    run_resolution,
)
from public_records_tracker.structured import ensure_structured_schema, upsert_entity


def test_aggressive_org_normalisation():
    assert normalize_org_name("The Acme & Sons Limited") == "acme and sons"
    assert normalize_org_name("ACME & SONS LTD.") == "acme and sons"


def test_exact_identifier_auto_resolves(tmp_path: Path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    ensure_resolution_schema(con)
    left = upsert_entity(
        con,
        entity_type="SUPPLIER",
        name="Acme Services Ltd",
        namespace="contracts",
    )
    right = upsert_entity(
        con,
        entity_type="ORGANISATION",
        name="ACME Services Limited",
        namespace="register",
    )
    add_identifier(con, entity_id=left, scheme="GB-COH", identifier="01234567")
    add_identifier(con, entity_id=right, scheme="GB-COH", identifier="01234567")
    run_resolution(con)
    match = con.execute(
        """SELECT confidence,status,match_method
           FROM entity_matches
           WHERE left_entity_id IN (?,?) AND right_entity_id IN (?,?)
             AND match_method='identifier_exact'""",
        [left, right, left, right],
    ).fetchone()
    assert match == (1.0, "auto_accepted", "identifier_exact")
    resolved = con.execute(
        """SELECT count(DISTINCT canonical_entity_id)
           FROM resolved_entity_members
           WHERE entity_id IN (?,?)""",
        [left, right],
    ).fetchone()[0]
    assert resolved == 1
    con.close()


def test_exact_name_without_identifier_requires_review(tmp_path: Path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    ensure_resolution_schema(con)
    left = upsert_entity(
        con,
        entity_type="SUPPLIER",
        name="Acme Services Ltd",
        namespace="payments",
    )
    right = upsert_entity(
        con,
        entity_type="ORGANISATION",
        name="ACME Services Limited",
        namespace="contracts",
    )

    run_resolution(con)

    match = con.execute(
        """SELECT status,match_method
           FROM entity_matches
           WHERE left_entity_id IN (?,?) AND right_entity_id IN (?,?)
             AND match_method='normalized_name_exact'""",
        [left, right, left, right],
    ).fetchone()
    assert match == ("review", "normalized_name_exact")
    assert con.execute(
        """SELECT count(*) FROM review_queue
           WHERE item_type='ENTITY_MATCH' AND status='open'"""
    ).fetchone()[0] == 1
    resolved = con.execute(
        """SELECT count(DISTINCT canonical_entity_id)
           FROM resolved_entity_members
           WHERE entity_id IN (?,?)""",
        [left, right],
    ).fetchone()[0]
    assert resolved == 2
    con.close()


def test_existing_auto_name_match_is_downgraded_on_rerun(tmp_path: Path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    ensure_resolution_schema(con)
    left = upsert_entity(
        con,
        entity_type="SUPPLIER",
        name="Shared Trading Ltd",
        namespace="payments",
    )
    right = upsert_entity(
        con,
        entity_type="ORGANISATION",
        name="Shared Trading Limited",
        namespace="contracts",
    )
    run_resolution(con)
    match_id = con.execute(
        """SELECT match_id FROM entity_matches
           WHERE match_method='normalized_name_exact'"""
    ).fetchone()[0]
    con.execute(
        "UPDATE entity_matches SET status='auto_accepted' WHERE match_id=?",
        [match_id],
    )

    run_resolution(con)

    assert con.execute(
        "SELECT status FROM entity_matches WHERE match_id=?", [match_id]
    ).fetchone()[0] == "review"
    resolved = con.execute(
        """SELECT count(DISTINCT canonical_entity_id)
           FROM resolved_entity_members
           WHERE entity_id IN (?,?)""",
        [left, right],
    ).fetchone()[0]
    assert resolved == 2
    con.close()
