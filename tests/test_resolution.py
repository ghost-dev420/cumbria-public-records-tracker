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


def test_resolution_refresh_is_idempotent_after_reopen(tmp_path: Path):
    db_path = tmp_path / "tracker.duckdb"
    con = connect(db_path)
    ensure_structured_schema(con)
    ensure_resolution_schema(con)
    entity_id = upsert_entity(
        con,
        entity_type="COUNCIL",
        name="Westmorland and Furness Council",
        namespace="modern_gov",
        metadata={
            "source_system": "ModernGov XML",
            "identifiers": [
                {"scheme": "council-id", "id": "westmorland-and-furness"}
            ],
        },
    )
    run_resolution(con)
    con.execute("CHECKPOINT")
    con.close()

    con = connect(db_path)
    ensure_structured_schema(con)
    ensure_resolution_schema(con)
    for _ in range(5):
        run_resolution(con)

    aliases = con.execute(
        """SELECT alias_text,normalized_alias
           FROM entity_aliases WHERE entity_id=?""",
        [entity_id],
    ).fetchall()
    assert aliases == [
        ("Westmorland and Furness Council", "westmorland and furness council")
    ]
    assert con.execute(
        "SELECT count(*) FROM entity_identifiers WHERE entity_id=?", [entity_id]
    ).fetchone()[0] == 1
    con.execute("CHECKPOINT")
    con.close()
