from public_records_tracker.db import connect
from public_records_tracker.public_export import prepare_public_db


def test_public_db_withholds_unapproved_claims_and_their_evidence(tmp_path):
    source = tmp_path / "working.duckdb"
    con = connect(source)
    con.execute(
        """INSERT INTO claims(claim_id,claim_text,classification,status)
           VALUES
             ('approved','Approved claim','documented','approved'),
             ('open','Unreviewed claim','analysis','open')"""
    )
    con.execute(
        """INSERT INTO claim_evidence(
             claim_id,document_id,snapshot_id,locator,support_type
           ) VALUES
             ('approved','doc-a','snap-a','page 1','supports'),
             ('open','doc-b','snap-b','page 2','supports')"""
    )
    con.close()

    public = tmp_path / "public.duckdb"
    prepare_public_db(source, public)

    con = connect(public)
    assert con.execute("SELECT claim_id FROM claims").fetchall() == [("approved",)]
    assert con.execute("SELECT claim_id FROM claim_evidence").fetchall() == [
        ("approved",)
    ]
    con.close()

    con = connect(source)
    assert con.execute("SELECT count(*) FROM claims").fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM claim_evidence").fetchone()[0] == 2
    con.close()
