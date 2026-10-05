from pathlib import Path

from public_records_tracker.config import load_sources
from public_records_tracker.db import connect, register_organisation
from public_records_tracker.payment_payer_hygiene import repair_payment_payer_attribution
from public_records_tracker.structured import add_fact, ensure_structured_schema, upsert_entity


def test_reanalysis_repairs_legacy_allerdale_payment_payer_idempotently(tmp_path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    register_organisation(
        con,
        {
            "id": "cumberland_council",
            "name": "Cumberland Council",
            "organisation_type": "unitary_authority",
            "status": "current",
        },
    )
    register_organisation(
        con,
        {
            "id": "allerdale_borough_council",
            "name": "Allerdale Borough Council",
            "organisation_type": "district_council",
            "status": "legacy",
        },
    )

    con.execute(
        """INSERT INTO documents(
             document_id,source_id,canonical_url,title,published_at,evidence_class,
             latest_snapshot_id,metadata_json
           ) VALUES (?,?,?,?,?,?,?,?)""",
        [
            "doc-legacy",
            "cumberland_transparency",
            "https://www.cumberland.gov.uk/sites/default/files/2025-03/allerdale_spending_may_2022.xlsx",
            "Allerdale spending May 2022",
            None,
            "OFFICIAL_RECORD",
            "snap-legacy",
            "{}",
        ],
    )

    old_payer = upsert_entity(
        con,
        entity_type="COUNCIL",
        name="Cumberland Council",
        namespace="organisation:cumberland_council",
        metadata={"organisation_id": "cumberland_council"},
    )
    supplier = upsert_entity(
        con,
        entity_type="SUPPLIER",
        name="Example Supplier Ltd",
        namespace="payments:cumberland_transparency",
    )
    old_fact = add_fact(
        con,
        document_id="doc-legacy",
        snapshot_id="snap-legacy",
        fact_type="PAYMENT",
        predicate="PAYMENT_TO_SUPPLIER",
        evidence_class="OFFICIAL_RECORD",
        subject_entity_id=old_payer,
        object_entity_id=supplier,
        value_text="50000.00",
        locator="XLSX Sheet1!row 10",
        metadata={"payment_date": "2022-05-01"},
    )

    source = next(
        item
        for item in load_sources(Path("config/sources.yml"))
        if item["id"] == "cumberland_transparency"
    )
    first = repair_payment_payer_attribution(con, source)
    second = repair_payment_payer_attribution(con, source)

    assert first == {"documents_matched": 1, "facts_repaired": 1}
    assert second == {"documents_matched": 0, "facts_repaired": 0}

    active = con.execute(
        """SELECT payer.canonical_name
           FROM facts f
           JOIN entities payer ON payer.entity_id=f.subject_entity_id
           WHERE f.document_id='doc-legacy'
             AND f.predicate='PAYMENT_TO_SUPPLIER'"""
    ).fetchall()
    assert active == [("Allerdale Borough Council",)]
    assert con.execute(
        "SELECT predicate FROM facts WHERE fact_id=?", [old_fact]
    ).fetchone()[0] == "SUPERSEDED_PAYMENT_TO_SUPPLIER"
    con.close()
