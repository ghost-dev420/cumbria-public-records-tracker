import hashlib
import json
import zipfile

from public_records_tracker.analysis import add_signal, ensure_analysis_schema
from public_records_tracker.db import connect
from public_records_tracker.evidence_packages import build_evidence_packages
from public_records_tracker.structured import add_fact, ensure_structured_schema, upsert_entity


def test_approved_signal_gets_verifiable_zip_manifest(tmp_path):
    db = tmp_path / "tracker.duckdb"
    con = connect(db)
    ensure_structured_schema(con)
    ensure_analysis_schema(con)

    supplier = upsert_entity(
        con,
        entity_type="SUPPLIER",
        name="Example Engineering Ltd",
        namespace="test",
    )
    con.execute(
        """INSERT INTO snapshots(
             snapshot_id,source_id,canonical_url,retrieved_at,sha256,archive_path,
             content_type,status_code,etag,last_modified
           ) VALUES (
             'snap-1','test-source','https://example.gov.uk/record',
             '2026-10-01T12:00:00+00:00',
             'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
             'data/raw/blobs/sha256/aa/aa/file.csv','text/csv',200,NULL,NULL
           )"""
    )
    con.execute(
        """INSERT INTO documents(
             document_id,source_id,canonical_url,title,published_at,evidence_class,
             latest_snapshot_id,metadata_json
           ) VALUES (
             'doc-1','test-source','https://example.gov.uk/record','Official payment record',
             '2026-10-01','OFFICIAL_RECORD','snap-1','{}'
           )"""
    )
    fact_id = add_fact(
        con,
        document_id="doc-1",
        snapshot_id="snap-1",
        fact_type="PAYMENT",
        predicate="PAYMENT_TO_SUPPLIER",
        evidence_class="OFFICIAL_RECORD",
        object_entity_id=supplier,
        value_text="1234.50",
        locator="CSV row 42",
        metadata={"currency": "GBP", "payment_date": "2026-09-30"},
    )
    signal_id = add_signal(
        con,
        signal_type="DOCUMENTED_PAYMENT_LINK",
        score=0.2,
        summary="Published payment record linked to supplier",
        subject=None,
        object_=supplier,
        evidence=[fact_id],
    )
    con.execute("UPDATE signals SET status='approved' WHERE signal_id=?", [signal_id])
    con.close()

    out = tmp_path / "site"
    assert build_evidence_packages(db, out) == 1

    zip_path = out / "evidence-packages" / f"{signal_id}.zip"
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path) as archive:
        assert sorted(archive.namelist()) == ["VERIFY.txt", "manifest.json", "manifest.sha256"]
        manifest_bytes = archive.read("manifest.json")
        manifest = json.loads(manifest_bytes)
        verify = archive.read("VERIFY.txt").decode("utf-8")
        checksum = archive.read("manifest.sha256").decode("utf-8")

    evidence = manifest["evidence"][0]
    assert evidence["source_url"] == "https://example.gov.uk/record"
    assert evidence["locator"] == "CSV row 42"
    assert evidence["sha256"] == "a" * 64
    assert "CSV row 42" in verify
    assert "https://example.gov.uk/record" in verify
    assert hashlib.sha256(manifest_bytes).hexdigest() in checksum

    # Archive paths may be recorded as provenance metadata, but raw source bytes are not copied.
    assert not (out / "data" / "raw").exists()
