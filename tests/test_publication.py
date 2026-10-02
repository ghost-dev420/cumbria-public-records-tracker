from public_records_tracker.db import connect
from public_records_tracker.health import ensure_health_schema
from public_records_tracker.publication import publication_status_rows


def test_unchecked_dataset_is_not_reported_missing(tmp_path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_health_schema(con)
    rows = publication_status_rows(con)
    assert rows[0]["source_id"] == "westmorland_furness_spending"
    assert rows[0]["status"] == "not_checked"
    con.close()


def test_recent_successful_observation_is_present(tmp_path):
    con = connect(tmp_path / "tracker.duckdb")
    ensure_health_schema(con)
    con.execute(
        """INSERT INTO source_runs(
             run_id,source_id,started_at,finished_at,status,record_count,fact_count
           ) VALUES ('run-1','westmorland_furness_spending',now(),now(),'success',1,1)"""
    )
    con.execute(
        """INSERT INTO observations(
             observation_id,snapshot_id,source_id,canonical_url,retrieved_at,status_code,
             etag,last_modified,observation_path
           ) VALUES (
             'obs-1','snap-1','westmorland_furness_spending','https://example.test/spend.csv',
             now(),200,NULL,NULL,'data/raw/observations/obs-1.json'
           )"""
    )
    rows = publication_status_rows(con)
    assert rows[0]["status"] == "present"
    assert rows[0]["http_status"] is None
    con.close()
