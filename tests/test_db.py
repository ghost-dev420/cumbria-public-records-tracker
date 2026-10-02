from pathlib import Path

from public_records_tracker.archive import archive_record
from public_records_tracker.db import (
    connect,
    ingest,
    link_source_organisations,
    register_organisation,
    register_source,
)
from public_records_tracker.models import EvidenceClass, Record


def _record(body: bytes) -> Record:
    return Record(
        source_id="s", url="https://example.com/a", title="A", body=body,
        content_type="text/html", evidence_class=EvidenceClass.OFFICIAL_RECORD,
    )


def test_changed_content_creates_change_event(tmp_path: Path):
    con = connect(tmp_path / "t.duckdb")
    first = _record(b"one")
    ingest(con, first, archive_record(first, tmp_path / "raw"))
    second = _record(b"two")
    ingest(con, second, archive_record(second, tmp_path / "raw"))
    assert con.execute("select count(*) from changes").fetchone()[0] == 1
    assert con.execute("select count(*) from snapshots").fetchone()[0] == 2
    assert con.execute("select count(*) from observations").fetchone()[0] == 2
    con.close()


def test_unchanged_content_reuses_snapshot_but_records_observation(tmp_path: Path):
    con = connect(tmp_path / "t.duckdb")
    record = _record(b"same")
    ingest(con, record, archive_record(record, tmp_path / "raw"))
    ingest(con, record, archive_record(record, tmp_path / "raw"))
    assert con.execute("select count(*) from snapshots").fetchone()[0] == 1
    assert con.execute("select count(*) from observations").fetchone()[0] == 2
    assert con.execute("select count(*) from changes").fetchone()[0] == 0
    con.close()


def test_source_links_to_first_class_council(tmp_path: Path):
    con = connect(tmp_path / "t.duckdb")
    organisation = {
        "id": "workington_town_council",
        "name": "Workington Town Council",
        "organisation_type": "town_council",
        "status": "current",
    }
    source = {
        "id": "workington_records",
        "name": "Workington records",
        "kind": "html_listing",
        "evidence_class": "OFFICIAL_RECORD",
        "organisation_ids": ["workington_town_council"],
    }
    register_organisation(con, organisation)
    register_source(con, source)
    link_source_organisations(con, source)
    assert con.execute("select count(*) from organisations").fetchone()[0] == 1
    row = con.execute(
        "select organisation_id from source_organisations where source_id=?",
        ["workington_records"],
    ).fetchone()
    assert row == ("workington_town_council",)
    con.close()
