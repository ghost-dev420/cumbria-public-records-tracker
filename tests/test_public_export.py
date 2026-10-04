import json

from public_records_tracker.analysis import add_signal, ensure_analysis_schema
from public_records_tracker.db import connect
from public_records_tracker.public_export import build_public_site, prepare_public_db
from public_records_tracker.resolution import ensure_resolution_schema, put_review
from public_records_tracker.structured import ensure_structured_schema


def _working_db(tmp_path):
    path = tmp_path / "working.duckdb"
    con = connect(path)
    ensure_structured_schema(con)
    ensure_resolution_schema(con)
    ensure_analysis_schema(con)

    con.execute(
        """INSERT INTO entity_matches(
             match_id,left_entity_id,right_entity_id,confidence,match_method,status,evidence_json
           ) VALUES
             ('accepted-match','a','b',1.0,'identifier_exact','accepted','{}'),
             ('review-match','c','d',0.91,'name_similarity','review','{}')"""
    )
    put_review(
        con,
        item_type="ENTITY_MATCH",
        score=0.91,
        summary="Internal fuzzy candidate",
        subject="c",
        object_="d",
        evidence=["review-match"],
    )
    approved = add_signal(
        con,
        signal_type="DOCUMENTED_LINK",
        score=0.2,
        summary="Approved documented link",
        subject=None,
        object_=None,
        evidence=[],
    )
    withheld = add_signal(
        con,
        signal_type="REVIEW_ONLY",
        score=0.2,
        summary="Internal review signal",
        subject=None,
        object_=None,
        evidence=[],
    )
    con.execute("UPDATE signals SET status='approved' WHERE signal_id=?", [approved])
    con.close()
    return path, approved, withheld


def test_prepare_public_db_withholds_internal_review_material(tmp_path):
    source, approved, withheld = _working_db(tmp_path)
    public = tmp_path / "public.duckdb"

    stats = prepare_public_db(source, public)
    assert stats == {
        "withheld_entity_matches": 1,
        "withheld_review_items": 1,
        "withheld_signals": 1,
        "auto_published_observations": 0,
        "published_entity_matches": 1,
        "published_signals": 1,
    }

    con = connect(public)
    assert con.execute("SELECT match_id FROM entity_matches").fetchall() == [
        ("accepted-match",)
    ]
    assert con.execute("SELECT count(*) FROM review_queue").fetchone()[0] == 0
    assert con.execute("SELECT signal_id FROM signals").fetchall() == [(approved,)]
    con.close()

    # Sanitisation works on a copy; the internal working DB remains intact.
    con = connect(source)
    assert con.execute("SELECT count(*) FROM review_queue").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM signals").fetchone()[0] == 2
    assert con.execute("SELECT status FROM signals WHERE signal_id=?", [withheld]).fetchone()[0] == "review"
    con.close()


def test_safe_coverage_observations_auto_publish_but_relationship_leads_do_not(tmp_path):
    source, _, _ = _working_db(tmp_path)
    con = connect(source)
    safe = add_signal(
        con,
        signal_type="PAYMENT_STREAM_WITHOUT_TRACKED_PROCUREMENT_LINK",
        score=0.3,
        summary="Coverage gap",
        subject=None,
        object_=None,
        evidence=[],
        metadata={"interpretation": "source_discovery_review_not_a_finding"},
    )
    risky = add_signal(
        con,
        signal_type="DECLARED_INTEREST_SUPPLIER_OVERLAP",
        score=0.68,
        summary="Relationship lead",
        subject=None,
        object_=None,
        evidence=[],
        metadata={"interpretation": "review_required_not_a_finding"},
    )
    con.close()

    public = tmp_path / "public-safe.duckdb"
    stats = prepare_public_db(source, public)
    assert stats["auto_published_observations"] == 1

    con = connect(public)
    public_ids = {row[0] for row in con.execute("SELECT signal_id FROM signals").fetchall()}
    assert safe in public_ids
    assert risky not in public_ids
    con.close()

    con = connect(source)
    # Auto-publication happens only in the temporary/copy database.
    assert con.execute("SELECT status FROM signals WHERE signal_id=?", [safe]).fetchone()[0] == "review"
    con.close()


def test_public_site_has_no_review_queue_surface(tmp_path):
    source, approved, _ = _working_db(tmp_path)
    out = tmp_path / "site"

    stats = build_public_site(source, out)
    assert stats["published_signals"] == 1
    assert not (out / "api" / "review-queue.json").exists()

    api_index = (out / "api" / "index.json").read_text(encoding="utf-8")
    page = (out / "index.html").read_text(encoding="utf-8")
    signals = (out / "api" / "signals.json").read_text(encoding="utf-8")

    assert "review-queue" not in api_index
    assert "Human review queue" not in page
    assert "open_reviews" not in page
    assert "findings" in api_index
    assert (out / "findings.html").exists()
    assert (out / "api" / "findings.json").exists()
    assert approved in signals
    assert "Internal review signal" not in signals


def test_public_findings_explain_coverage_gap_without_calling_it_waste(tmp_path):
    source, _, _ = _working_db(tmp_path)
    con = connect(source)
    add_signal(
        con,
        signal_type="PAYMENTS_OUTSIDE_TRACKED_CONTRACT_PERIODS",
        score=0.45,
        summary="3 published payments fall outside tracked periods.",
        subject=None,
        object_=None,
        evidence=[],
        metadata={
            "interpretation": "review_required_not_a_finding",
            "payment_total_gbp": 12345.0,
            "caveats": ["The tracker may not contain every extension."],
        },
    )
    con.close()

    out = tmp_path / "site-findings"
    build_public_site(source, out)
    payload = json.loads((out / "api" / "findings.json").read_text(encoding="utf-8"))
    item = next(row for row in payload["findings"] if row["signal_type"] == "PAYMENTS_OUTSIDE_TRACKED_CONTRACT_PERIODS")
    assert item["category"] == "contract_coverage_gap"
    assert item["status_label"] == "Coverage gap"
    assert "not findings of wrongdoing" in payload["notice"]
    assert "waste" not in item["headline"].casefold()


def test_public_db_does_not_retain_private_archive_paths(tmp_path):
    source, _, _ = _working_db(tmp_path)
    con = connect(source)
    con.execute(
        """INSERT INTO snapshots(
             snapshot_id,source_id,canonical_url,retrieved_at,sha256,archive_path
           ) VALUES ('snap','src','https://example.test/doc',now(),'abc','data/raw/blobs/abc')"""
    )
    con.execute(
        """INSERT INTO observations(
             observation_id,snapshot_id,source_id,canonical_url,retrieved_at,observation_path
           ) VALUES ('obs','snap','src','https://example.test/doc',now(),'data/raw/observations/obs.json')"""
    )
    con.close()

    public = tmp_path / "public-paths.duckdb"
    prepare_public_db(source, public)
    con = connect(public)
    assert con.execute("SELECT archive_path FROM snapshots").fetchone()[0] == ""
    assert con.execute("SELECT observation_path FROM observations").fetchone()[0] == ""
    con.close()

    con = connect(source)
    assert con.execute("SELECT archive_path FROM snapshots").fetchone()[0] == "data/raw/blobs/abc"
    con.close()
