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
    assert approved in signals
    assert "Internal review signal" not in signals
