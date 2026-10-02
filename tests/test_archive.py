from pathlib import Path

from public_records_tracker.archive import archive_record, canonicalize_url, sha256_bytes
from public_records_tracker.models import EvidenceClass, Record


def test_canonicalize_url_sorts_query_and_removes_fragment():
    assert canonicalize_url("HTTPS://Example.COM/a?z=2&a=1#x") == "https://example.com/a?a=1&z=2"


def test_archive_is_content_addressed(tmp_path: Path):
    record = Record(
        source_id="test", url="https://example.com/doc.pdf", title="Doc",
        body=b"abc", content_type="application/pdf",
        evidence_class=EvidenceClass.OFFICIAL_RECORD,
    )
    first = archive_record(record, tmp_path)
    second = archive_record(record, tmp_path)
    assert first.sha256 == sha256_bytes(b"abc")
    assert first.archive_path == second.archive_path
    assert first.snapshot_id == second.snapshot_id
    assert first.observation_id != second.observation_id
    assert Path(first.archive_path).read_bytes() == b"abc"
    assert Path(first.metadata_path).exists()
    assert Path(first.observation_path).exists()
    assert Path(second.observation_path).exists()
