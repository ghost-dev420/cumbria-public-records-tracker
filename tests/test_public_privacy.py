from public_records_tracker.db import connect
from public_records_tracker.diffs import ensure_diff_schema
from public_records_tracker.public_export import (
    PUBLIC_NARRATIVE_REDACTION,
    prepare_public_db,
)
from public_records_tracker.structured import add_fact, ensure_structured_schema


def test_public_copy_redacts_ombudsman_case_narrative_but_keeps_working_copy(tmp_path):
    working = tmp_path / "working.duckdb"
    con = connect(working)
    ensure_structured_schema(con)
    ensure_diff_schema(con)
    fact_id = add_fact(
        con,
        document_id="doc",
        snapshot_id="snap-new",
        fact_type="OMBUDSMAN_SUMMARY",
        predicate="HAS_OMBUDSMAN_SUMMARY",
        evidence_class="REGULATORY_FINDING",
        value_text="Sensitive health and family narrative from the published case summary.",
        locator="final decision summary",
    )
    con.execute(
        """INSERT INTO structured_changes(
             change_id,document_id,old_snapshot_id,new_snapshot_id,change_type,
             fact_type,predicate,value_text,new_fact_id
           ) VALUES (?,?,?,?,?,?,?,?,?)""",
        [
            "change",
            "doc",
            "snap-old",
            "snap-new",
            "FACT_ADDED",
            "OMBUDSMAN_SUMMARY",
            "HAS_OMBUDSMAN_SUMMARY",
            "Sensitive health and family narrative from the published case summary.",
            fact_id,
        ],
    )
    con.close()

    public = tmp_path / "public.duckdb"
    prepare_public_db(working, public)

    con = connect(public)
    assert con.execute(
        "SELECT value_text FROM facts WHERE fact_id=?", [fact_id]
    ).fetchone()[0] == PUBLIC_NARRATIVE_REDACTION
    assert con.execute(
        "SELECT value_text FROM structured_changes WHERE change_id='change'"
    ).fetchone()[0] == PUBLIC_NARRATIVE_REDACTION
    con.close()

    con = connect(working)
    assert "Sensitive health" in con.execute(
        "SELECT value_text FROM facts WHERE fact_id=?", [fact_id]
    ).fetchone()[0]
    assert "Sensitive health" in con.execute(
        "SELECT value_text FROM structured_changes WHERE change_id='change'"
    ).fetchone()[0]
    con.close()
