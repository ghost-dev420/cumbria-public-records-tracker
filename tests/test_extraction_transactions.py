import pytest

from public_records_tracker.db import connect
from public_records_tracker.extractors import EXTRACTORS
from public_records_tracker.runner import _extract_record
from public_records_tracker.structured import add_fact, activate_snapshot, ensure_structured_schema


def _add_value(con, document_id: str, snapshot_id: str, value: str) -> None:
    add_fact(
        con,
        document_id=document_id,
        snapshot_id=snapshot_id,
        fact_type="TEST_FACT",
        predicate="HAS_TEST_VALUE",
        evidence_class="OFFICIAL_RECORD",
        value_text=value,
        locator="test",
    )


def test_failed_extraction_does_not_replace_last_successful_facts(tmp_path, monkeypatch):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)
    _add_value(con, "doc", "old", "old-good")
    activate_snapshot(
        con,
        document_id="doc",
        snapshot_id="old",
        observed_at="2026-01-01T00:00:00+00:00",
    )

    def failing_extractor(**kwargs):
        _add_value(kwargs["con"], kwargs["document_id"], kwargs["snapshot_id"], "partial-new")
        raise RuntimeError("parser failed halfway")

    monkeypatch.setitem(EXTRACTORS, "test_failing", failing_extractor)
    with pytest.raises(RuntimeError, match="parser failed halfway"):
        _extract_record(
            con,
            source={"id": "source", "kind": "html_listing", "extractors": ["test_failing"]},
            record=object(),
            document_id="doc",
            snapshot_id="new",
            observed_at="2026-02-01T00:00:00+00:00",
        )

    assert con.execute(
        "SELECT value_text,snapshot_id FROM latest_facts WHERE document_id='doc'"
    ).fetchall() == [("old-good", "old")]
    assert con.execute(
        "SELECT count(*) FROM facts WHERE document_id='doc' AND snapshot_id='new'"
    ).fetchone()[0] == 0
    con.close()


def test_reextract_same_snapshot_replaces_obsolete_facts(tmp_path, monkeypatch):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_structured_schema(con)

    def first_extractor(**kwargs):
        _add_value(kwargs["con"], kwargs["document_id"], kwargs["snapshot_id"], "keep")
        _add_value(kwargs["con"], kwargs["document_id"], kwargs["snapshot_id"], "obsolete")
        return 2

    monkeypatch.setitem(EXTRACTORS, "test_rebuild", first_extractor)
    source = {"id": "source", "kind": "html_listing", "extractors": ["test_rebuild"]}
    facts, _ = _extract_record(
        con,
        source=source,
        record=object(),
        document_id="doc",
        snapshot_id="same",
        observed_at="2026-01-01T00:00:00+00:00",
    )
    assert facts == 2

    def second_extractor(**kwargs):
        _add_value(kwargs["con"], kwargs["document_id"], kwargs["snapshot_id"], "keep")
        return 1

    monkeypatch.setitem(EXTRACTORS, "test_rebuild", second_extractor)
    facts, _ = _extract_record(
        con,
        source=source,
        record=object(),
        document_id="doc",
        snapshot_id="same",
        observed_at="2026-01-02T00:00:00+00:00",
    )
    assert facts == 1
    assert con.execute(
        "SELECT value_text FROM latest_facts WHERE document_id='doc' ORDER BY value_text"
    ).fetchall() == [("keep",)]
    con.close()
