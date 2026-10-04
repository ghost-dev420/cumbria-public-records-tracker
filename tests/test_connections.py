from pathlib import Path

from public_records_tracker.analysis import ensure_analysis_schema
from public_records_tracker.connections import detect_psc_supplier_connections
from public_records_tracker.db import connect
from public_records_tracker.reference import ensure_reference_schema
from public_records_tracker.resolution import ensure_resolution_schema
from public_records_tracker.structured import add_fact, ensure_structured_schema, upsert_entity


def _base_db(path: Path):
    con = connect(path)
    ensure_structured_schema(con)
    ensure_resolution_schema(con)
    ensure_analysis_schema(con)
    ensure_reference_schema(con)
    con.execute(
        """INSERT INTO documents(
             document_id,source_id,canonical_url,title,evidence_class,latest_snapshot_id,metadata_json
           ) VALUES ('doc','src','https://example.test/source','Source','OFFICIAL_RECORD','snap','{}')"""
    )
    con.execute(
        """INSERT INTO snapshots(
             snapshot_id,source_id,canonical_url,retrieved_at,sha256,archive_path
           ) VALUES ('snap','src','https://example.test/source',now(),'abc','')"""
    )
    return con


def test_declared_interest_naming_psc_of_supplier_is_queued_not_published(tmp_path):
    con = _base_db(tmp_path / "tracker.duckdb")
    councillor = upsert_entity(
        con,
        entity_type="PERSON",
        name="John Example",
        namespace="council",
        metadata={"source_system": "ModernGov"},
    )
    psc = upsert_entity(
        con,
        entity_type="PERSON",
        name="Jane Example",
        namespace="companies_house_psc",
    )
    company = upsert_entity(
        con,
        entity_type="COMPANY",
        name="Example Services Limited",
        namespace="companies_house",
    )
    add_fact(
        con,
        document_id="doc",
        snapshot_id="snap",
        fact_type="REGISTER_OF_INTEREST_ENTRY",
        predicate="DECLARED_INTEREST",
        evidence_class="OFFICIAL_RECORD",
        subject_entity_id=councillor,
        value_text="My wife Jane Example is a director of Example Services Limited",
        locator="register question 1",
    )
    add_fact(
        con,
        document_id="doc",
        snapshot_id="snap",
        fact_type="PAYMENT",
        predicate="PAYMENT_TO_SUPPLIER",
        evidence_class="OFFICIAL_RECORD",
        subject_entity_id=None,
        object_entity_id=company,
        value_text="50000",
        locator="payment row",
    )
    con.execute(
        """INSERT INTO registry_relationships(
             relationship_id,registry,predicate,subject_entity_id,object_entity_id,
             source_url,dataset_date,metadata_json
           ) VALUES ('psc-rel','companies_house','PSC_OF_COMPANY',?,?,
                     'https://download.companieshouse.gov.uk/psc.zip','2026-10-01',
                     '{"natures_of_control":["ownership-of-shares-25-to-50-percent"]}')""",
        [psc, company],
    )

    stats = detect_psc_supplier_connections(con)
    assert stats["psc_declared_interest_candidates"] == 1
    signal = con.execute(
        """SELECT status,metadata_json FROM signals
           WHERE signal_type='DECLARED_INTEREST_PSC_SUPPLIER_LINK'"""
    ).fetchone()
    assert signal is not None
    assert signal[0] == "review"
    assert '"wife"' in signal[1]
    review = con.execute(
        """SELECT count(*) FROM review_queue
           WHERE item_type='SIGNAL_REVIEW' AND status='open'"""
    ).fetchone()[0]
    assert review == 1
    con.close()


def test_exact_psc_officeholder_name_creates_identity_candidate_only(tmp_path):
    con = _base_db(tmp_path / "tracker.duckdb")
    councillor = upsert_entity(
        con,
        entity_type="PERSON",
        name="Alex Example",
        namespace="council",
        metadata={"source_system": "ModernGov"},
    )
    psc = upsert_entity(
        con,
        entity_type="PERSON",
        name="Alex Example",
        namespace="companies_house_psc",
    )
    company = upsert_entity(
        con,
        entity_type="COMPANY",
        name="Example Works Limited",
        namespace="companies_house",
    )
    committee = upsert_entity(con, entity_type="COMMITTEE", name="Audit Committee")
    add_fact(
        con,
        document_id="doc",
        snapshot_id="snap",
        fact_type="COMMITTEE_MEMBERSHIP",
        predicate="MEMBER_OF",
        evidence_class="OFFICIAL_RECORD",
        subject_entity_id=councillor,
        object_entity_id=committee,
        locator="committee membership",
    )
    add_fact(
        con,
        document_id="doc",
        snapshot_id="snap",
        fact_type="CONTRACT_SUPPLIER",
        predicate="SUPPLIER_TO_CONTRACT",
        evidence_class="OFFICIAL_RECORD",
        subject_entity_id=company,
        object_entity_id=None,
        locator="contract row",
    )
    con.execute(
        """INSERT INTO registry_relationships(
             relationship_id,registry,predicate,subject_entity_id,object_entity_id,
             source_url,dataset_date,metadata_json
           ) VALUES ('psc-rel','companies_house','PSC_OF_COMPANY',?,?,
                     'https://download.companieshouse.gov.uk/psc.zip','2026-10-01','{}')""",
        [psc, company],
    )

    stats = detect_psc_supplier_connections(con)
    assert stats["psc_officeholder_candidates"] == 1
    signal = con.execute(
        """SELECT status,summary FROM signals
           WHERE signal_type='PSC_SUPPLIER_PUBLIC_OFFICEHOLDER_CANDIDATE'"""
    ).fetchone()
    assert signal is not None
    assert signal[0] == "review"
    assert "same normalized name" in signal[1]
    con.close()
