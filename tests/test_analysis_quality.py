from public_records_tracker.analysis_quality import prepare_analysis_resolution
from public_records_tracker.db import connect
from public_records_tracker.resolution import add_identifier, ensure_resolution_schema
from public_records_tracker.structured import ensure_structured_schema, upsert_entity


def test_payment_channel_alias_merges_into_clean_procurement_entity(tmp_path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    ensure_resolution_schema(con)

    procurement = upsert_entity(
        con,
        entity_type="SUPPLIER",
        name="NPOWER Limited",
        namespace="contracts_finder",
        metadata={"source_system": "Contracts Finder"},
    )
    dirty_payment = upsert_entity(
        con,
        entity_type="SUPPLIER",
        name="NPOWER LIMITED (NCR)",
        namespace="payments:test",
        metadata={"source_system": "Council spending"},
    )
    assert procurement != dirty_payment

    stats = prepare_analysis_resolution(con)
    assert stats["payment_channel_matches"] >= 1

    match = con.execute(
        """SELECT status,match_method
           FROM entity_matches
           WHERE (left_entity_id=? AND right_entity_id=?)
              OR (left_entity_id=? AND right_entity_id=?)""",
        [procurement, dirty_payment, dirty_payment, procurement],
    ).fetchone()
    assert match == ("auto_accepted", "payment_channel_normalized")

    rows = dict(
        con.execute(
            """SELECT entity_id,canonical_entity_id
               FROM resolved_entity_members
               WHERE entity_id IN (?,?)""",
            [procurement, dirty_payment],
        ).fetchall()
    )
    assert rows[procurement] == procurement
    assert rows[dirty_payment] == procurement
    con.close()


def test_identifier_backed_entity_is_preferred_representative(tmp_path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    ensure_resolution_schema(con)

    identified = upsert_entity(
        con,
        entity_type="COMPANY",
        name="Example Infrastructure Limited",
        namespace="registry",
        metadata={"source_system": "Companies House"},
    )
    payment_alias = upsert_entity(
        con,
        entity_type="SUPPLIER",
        name="Example Infrastructure Ltd (CHAPS)",
        namespace="payments:test",
        metadata={"source_system": "Council spending"},
    )
    add_identifier(
        con,
        entity_id=identified,
        scheme="GB-COH",
        identifier="01234567",
        source="Companies House",
    )

    prepare_analysis_resolution(con)
    row = con.execute(
        "SELECT canonical_entity_id FROM resolved_entity_members WHERE entity_id=?",
        [payment_alias],
    ).fetchone()
    assert row == (identified,)
    con.close()
