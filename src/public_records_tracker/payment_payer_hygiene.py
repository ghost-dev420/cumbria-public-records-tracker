from __future__ import annotations

import json

import duckdb

from .extractors.payments import _payer_entity, _payer_organisation_id
from .models import EvidenceClass, Record
from .structured import add_fact


def repair_payment_payer_attribution(
    con: duckdb.DuckDBPyConnection,
    source: dict,
) -> dict[str, int]:
    """Repair active payment facts whose document identifies a predecessor payer.

    Some successor-authority transparency pages host historic spending files from
    predecessor councils. Older tracker configurations assigned every payment in
    those files to the successor authority. Current source configuration carries
    narrow ``payment_payer_rules`` based on the document title/URL.

    This repair deliberately avoids updating or deleting indexed fact rows. A
    corrected fact is inserted with the same provenance and value but the proper
    payer entity, then the older active fact is retired by changing only its
    unindexed predicate. It is therefore safe to rerun and mirrors the payment
    extractors' re-extraction behaviour.
    """
    source_id = str(source.get("id") or "").strip()
    rules = source.get("payment_payer_rules") or []
    if not source_id or not rules:
        return {"documents_matched": 0, "facts_repaired": 0}

    rows = con.execute(
        """SELECT d.document_id,d.canonical_url,d.title,d.evidence_class,d.metadata_json,
                  f.fact_id,f.snapshot_id,f.fact_type,f.subject_entity_id,
                  f.object_entity_id,f.value_text,f.locator,f.evidence_class,f.metadata_json
           FROM documents d
           JOIN facts f ON f.document_id=d.document_id
           WHERE d.source_id=?
             AND f.predicate='PAYMENT_TO_SUPPLIER'""",
        [source_id],
    ).fetchall()

    matched_documents: set[str] = set()
    repaired = 0
    for (
        document_id,
        canonical_url,
        title,
        document_evidence_class,
        document_metadata_json,
        fact_id,
        snapshot_id,
        fact_type,
        old_payer,
        supplier,
        value_text,
        locator,
        fact_evidence_class,
        fact_metadata_json,
    ) in rows:
        try:
            document_metadata = json.loads(document_metadata_json or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            document_metadata = {}

        try:
            evidence = EvidenceClass(str(document_evidence_class))
        except ValueError:
            evidence = EvidenceClass.OFFICIAL_RECORD

        record = Record(
            source_id=source_id,
            url=str(canonical_url),
            title=str(title or ""),
            body=b"",
            content_type="",
            evidence_class=evidence,
            metadata=document_metadata if isinstance(document_metadata, dict) else {},
        )
        organisation_id = _payer_organisation_id(source, record)
        default_organisation_id = str(source.get("payment_payer_organisation_id") or "")

        # This migration exists to correct known predecessor files. Leave normal
        # successor-authority documents alone, including any facts whose payer was
        # established by another extractor-specific rule.
        if not organisation_id or organisation_id == default_organisation_id:
            continue

        new_payer = _payer_entity(con, source, record)
        if not new_payer or new_payer == old_payer:
            continue

        try:
            fact_metadata = json.loads(fact_metadata_json or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            fact_metadata = {}

        new_fact_id = add_fact(
            con,
            document_id=str(document_id),
            snapshot_id=str(snapshot_id),
            fact_type=str(fact_type),
            predicate="PAYMENT_TO_SUPPLIER",
            evidence_class=str(fact_evidence_class),
            subject_entity_id=new_payer,
            object_entity_id=supplier,
            value_text=value_text,
            locator=locator,
            metadata=fact_metadata if isinstance(fact_metadata, dict) else {},
        )
        if new_fact_id == fact_id:
            continue

        con.execute(
            """UPDATE facts
               SET predicate='SUPERSEDED_PAYMENT_TO_SUPPLIER'
               WHERE fact_id=? AND predicate='PAYMENT_TO_SUPPLIER'""",
            [fact_id],
        )
        matched_documents.add(str(document_id))
        repaired += 1

    return {
        "documents_matched": len(matched_documents),
        "facts_repaired": repaired,
    }
