from public_records_tracker.db import connect
from public_records_tracker.public_export import build_public_site


def test_public_site_build_removes_stale_files_from_previous_build(tmp_path):
    db_path = tmp_path / "tracker.duckdb"
    connect(db_path).close()
    out = tmp_path / "site"
    stale = out / "evidence-packages" / "stale-signal.zip"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old public artifact")
    (out / "api" / "entities").mkdir(parents=True)
    (out / "api" / "entities" / "stale-entity.json").write_text("{}")

    build_public_site(db_path, out)

    assert not stale.exists()
    assert not (out / "api" / "entities" / "stale-entity.json").exists()
    assert (out / "index.html").exists()
