from public_records_tracker.analysis_quality import prepare_analysis_resolution
from public_records_tracker.db import connect
from public_records_tracker.resolution import ensure_resolution_schema, put_review
from public_records_tracker.structured import ensure_structured_schema, upsert_entity


def _setup(tmp_path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    ensure_resolution_schema(con)
    return con


def _same_component(con, left: str, right: str) -> bool:
    rows = con.execute(
        """SELECT entity_id,canonical_entity_id
           FROM resolved_entity_members
           WHERE entity_id IN (?,?)""",
        [left, right],
    ).fetchall()
    return len(rows) == 2 and len({row[1] for row in rows}) == 1


def test_legal_suffix_variants_auto_resolve_and_close_review(tmp_path):
    con = _setup(tmp_path)
    left = upsert_entity(con, entity_type="SUPPLIER", name="NPOWER", namespace="payments")
    right = upsert_entity(
        con,
        entity_type="SUPPLIER",
        name="NPOWER LIMITED",
        namespace="contracts",
    )
    put_review(
        con,
        item_type="ENTITY_MATCH",
        score=0.93,
        summary="Possible entity match on normalized name: npower",
        subject=left,
        object_=right,
        evidence=["old-review"],
        metadata={"method": "normalized_name_exact"},
    )

    stats = prepare_analysis_resolution(con)

    assert stats["legal_suffix_matches"] == 1
    assert stats["resolved_entity_match_reviews"] == 1
    assert _same_component(con, left, right)
    assert con.execute(
        "SELECT status FROM review_queue WHERE subject_entity_id=? AND object_entity_id=?",
        [left, right],
    ).fetchone()[0] == "resolved"
    con.close()


def test_long_one_edit_typo_auto_resolves(tmp_path):
    con = _setup(tmp_path)
    typo = upsert_entity(
        con,
        entity_type="SUPPLIER",
        name="Morgan Sindall Construction & Infrastructire Ltd",
        namespace="payments",
    )
    clean = upsert_entity(
        con,
        entity_type="SUPPLIER",
        name="Morgan Sindall Construction & Infrastructure Ltd",
        namespace="contracts",
    )
    put_review(
        con,
        item_type="ENTITY_MATCH",
        score=0.978,
        summary="Similar organisation names",
        subject=typo,
        object_=clean,
        evidence=["fuzzy-review"],
        metadata={"method": "name_similarity"},
    )

    stats = prepare_analysis_resolution(con)

    assert stats["near_exact_typo_matches"] == 1
    assert stats["resolved_entity_match_reviews"] == 1
    assert _same_component(con, typo, clean)
    assert con.execute(
        """SELECT count(*) FROM entity_matches
           WHERE match_method='near_exact_typo' AND status='auto_accepted'"""
    ).fetchone()[0] == 1
    con.close()


def test_short_similar_names_remain_manual(tmp_path):
    con = _setup(tmp_path)
    left = upsert_entity(con, entity_type="SUPPLIER", name="Alpha Services", namespace="a")
    right = upsert_entity(con, entity_type="SUPPLIER", name="Alpha Service", namespace="b")

    stats = prepare_analysis_resolution(con)

    assert stats["near_exact_typo_matches"] == 0
    assert not _same_component(con, left, right)
    con.close()


def test_payment_channel_review_is_closed_after_auto_resolution(tmp_path):
    con = _setup(tmp_path)
    dirty = upsert_entity(
        con,
        entity_type="SUPPLIER",
        name="CUMBRIA COUNTY COUNCIL (CHAPS)",
        namespace="payments",
    )
    clean = upsert_entity(
        con,
        entity_type="COUNCIL",
        name="CUMBRIA COUNTY COUNCIL",
        namespace="organisation",
    )
    put_review(
        con,
        item_type="ENTITY_MATCH",
        score=0.88,
        summary="Similar organisation names",
        subject=dirty,
        object_=clean,
        evidence=["old-fuzzy-review"],
        metadata={"method": "name_similarity"},
    )

    stats = prepare_analysis_resolution(con)

    assert stats["payment_channel_matches"] == 1
    assert stats["resolved_entity_match_reviews"] == 1
    assert _same_component(con, dirty, clean)
    con.close()
