from pathlib import Path

from public_records_tracker.db import connect
from public_records_tracker.entity_pages import build_entity_pages
from public_records_tracker.structured import add_fact, ensure_structured_schema, upsert_entity


def test_builds_entity_timeline_with_source_and_hash(tmp_path: Path):
    db = tmp_path / "tracker.duckdb"
    out = tmp_path / "site"
    con = connect(db)
    ensure_structured_schema(con)
    entity = upsert_entity(con, entity_type="SUPPLIER", name="Example Services Ltd")
    con.execute(
        """INSERT INTO snapshots(snapshot_id,source_id,canonical_url,retrieved_at,sha256,archive_path,content_type,status_code,etag,last_modified)
           VALUES ('snap','source','https://example.gov/record','2026-10-01','abc123','raw','text/html',200,NULL,NULL)"""
    )
    con.execute(
        """INSERT INTO documents(document_id,source_id,canonical_url,title,published_at,evidence_class,latest_snapshot_id,metadata_json)
           VALUES ('doc','source','https://example.gov/record','Official record','2026-09-30','OFFICIAL_RECORD','snap','{}')"""
    )
    add_fact(
        con,
        document_id="doc",
        snapshot_id="snap",
        fact_type="PAYMENT_AMOUNT",
        predicate="HAS_PAYMENT_AMOUNT",
        evidence_class="OFFICIAL_RECORD",
        subject_entity_id=entity,
        value_text="1250.00",
        locator="CSV row 42",
    )
    con.close()

    build_entity_pages(db, out)

    index = (out / "entities.html").read_text(encoding="utf-8")
    page = (out / "entities" / f"{entity}.html").read_text(encoding="utf-8")
    api = (out / "api" / "entities" / f"{entity}.json").read_text(encoding="utf-8")
    assert "Example Services Ltd" in index
    assert "CSV row 42" in page
    assert "https://example.gov/record" in page
    assert "abc123" in page
    assert "PAYMENT_AMOUNT" in api
