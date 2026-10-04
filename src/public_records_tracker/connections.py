from __future__ import annotations

import json
import re
from collections import defaultdict

import duckdb

from .analysis import add_signal
from .analysis_quality import prepare_analysis_resolution
from .reference import ensure_reference_schema
from .resolution import ensure_resolution_schema, put_review
from .structured import normalize_name


_RELATIONSHIP_TERMS = (
    "wife",
    "husband",
    "spouse",
    "civil partner",
    "partner",
    "son",
    "daughter",
    "brother",
    "sister",
    "mother",
    "father",
    "parent",
    "relative",
    "family",
)


def _canonical_map(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    ensure_resolution_schema(con)
    prepare_analysis_resolution(con)
    return {
        str(entity_id): str(canonical_id)
        for entity_id, canonical_id in con.execute(
            "SELECT entity_id,canonical_entity_id FROM resolved_entity_members"
        ).fetchall()
    }


def _supplier_evidence(con: duckdb.DuckDBPyConnection, canonical: dict[str, str]):
    evidence: dict[str, list[str]] = defaultdict(list)
    for fact_id, supplier_id in con.execute(
        """SELECT fact_id,object_entity_id
           FROM latest_facts
           WHERE predicate='PAYMENT_TO_SUPPLIER' AND object_entity_id IS NOT NULL"""
    ).fetchall():
        key = canonical.get(str(supplier_id), str(supplier_id))
        evidence[key].append(str(fact_id))
    for fact_id, supplier_id in con.execute(
        """SELECT fact_id,subject_entity_id
           FROM latest_facts
           WHERE predicate='SUPPLIER_TO_CONTRACT' AND subject_entity_id IS NOT NULL"""
    ).fetchall():
        key = canonical.get(str(supplier_id), str(supplier_id))
        evidence[key].append(str(fact_id))
    return evidence


def _officeholder_facts(con: duckdb.DuckDBPyConnection) -> dict[str, list[str]]:
    rows = con.execute(
        """SELECT e.entity_id,e.canonical_name,list(DISTINCT f.fact_id)
           FROM entities e
           JOIN latest_facts f ON f.subject_entity_id=e.entity_id
           WHERE e.entity_type='PERSON'
             AND f.predicate IN (
                 'MEMBER_OF','REPRESENTS_WARD','MEMBER_OF_PARTY',
                 'APPOINTED_TO_OUTSIDE_BODY','DECLARED_INTEREST',
                 'DECLARED_INTEREST_AT','ATTENDANCE_RECORDED_FOR'
             )
           GROUP BY e.entity_id,e.canonical_name"""
    ).fetchall()
    result: dict[str, list[str]] = defaultdict(list)
    for entity_id, name, fact_ids in rows:
        key = normalize_name(str(name))
        if len(key.split()) < 2 or len(key) < 6:
            continue
        result[key].extend(str(item) for item in fact_ids or [])
        result[key].append(f"entity:{entity_id}")
    return result


def _declared_interest_rows(con: duckdb.DuckDBPyConnection):
    return con.execute(
        """SELECT fact_id,subject_entity_id,value_text
           FROM latest_facts
           WHERE predicate='DECLARED_INTEREST'
             AND value_text IS NOT NULL
             AND value_text <> '[REDACTED_FROM_STRUCTURED_DATA]'"""
    ).fetchall()


def detect_psc_supplier_connections(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Queue evidence-led PSC/supplier relationship candidates for human review.

    No relationship candidate is auto-published. Exact public-officeholder name
    matches are still review items because names alone do not prove identity.
    Declared-interest text matches are stronger, but likewise remain private until
    a human has checked the underlying declaration and Companies House record.
    """
    ensure_reference_schema(con)
    canonical = _canonical_map(con)
    supplier_evidence = _supplier_evidence(con, canonical)
    officeholders = _officeholder_facts(con)
    interest_rows = _declared_interest_rows(con)

    stats = {
        "psc_supplier_relationships": 0,
        "psc_officeholder_candidates": 0,
        "psc_declared_interest_candidates": 0,
    }

    rows = con.execute(
        """SELECT r.relationship_id,r.subject_entity_id,r.object_entity_id,
                  r.source_url,r.dataset_date,r.metadata_json,
                  pe.canonical_name,ce.canonical_name
           FROM registry_relationships r
           JOIN entities pe ON pe.entity_id=r.subject_entity_id
           JOIN entities ce ON ce.entity_id=r.object_entity_id
           WHERE r.registry='companies_house'
             AND r.predicate='PSC_OF_COMPANY'"""
    ).fetchall()

    for (
        relationship_id,
        psc_entity_id,
        company_entity_id,
        source_url,
        dataset_date,
        relationship_metadata,
        psc_name,
        company_name,
    ) in rows:
        company_key = canonical.get(str(company_entity_id), str(company_entity_id))
        supplier_facts = sorted(set(supplier_evidence.get(company_key) or []))
        if not supplier_facts:
            continue
        stats["psc_supplier_relationships"] += 1

        try:
            psc_meta = json.loads(relationship_metadata or "{}")
        except json.JSONDecodeError:
            psc_meta = {}
        psc_key = normalize_name(str(psc_name))
        if len(psc_key.split()) < 2 or len(psc_key) < 6:
            continue

        common_metadata = {
            "companies_house_relationship_id": str(relationship_id),
            "companies_house_source_url": str(source_url),
            "companies_house_dataset_date": dataset_date,
            "psc_name": str(psc_name),
            "company_name": str(company_name),
            "natures_of_control": psc_meta.get("natures_of_control") or [],
            "interpretation": "review_required_not_a_finding",
        }

        if psc_key in officeholders:
            officeholder_fact_ids = [
                value for value in officeholders[psc_key] if not value.startswith("entity:")
            ]
            evidence = sorted(set(supplier_facts + officeholder_fact_ids))
            signal_id = add_signal(
                con,
                signal_type="PSC_SUPPLIER_PUBLIC_OFFICEHOLDER_CANDIDATE",
                score=0.76,
                summary=(
                    f"Companies House PSC data names {psc_name} for supplier {company_name}; "
                    "the same normalized name appears in public office-holder records."
                ),
                subject=str(psc_entity_id),
                object_=company_key,
                evidence=evidence,
                metadata=common_metadata
                | {"match_method": "exact_normalized_person_name"},
            )
            put_review(
                con,
                item_type="SIGNAL_REVIEW",
                score=0.76,
                summary=f"Verify PSC / public office-holder identity for {psc_name}",
                subject=str(psc_entity_id),
                object_=company_key,
                evidence=evidence,
                metadata={
                    "signal_id": signal_id,
                    "reason": "companies_house_psc_exact_name_matches_officeholder",
                    "companies_house_source_url": str(source_url),
                },
            )
            stats["psc_officeholder_candidates"] += 1

        padded_name = f" {psc_key} "
        for fact_id, declaring_person_id, text in interest_rows:
            normalized_text = f" {normalize_name(str(text))} "
            if padded_name not in normalized_text:
                continue
            lower_text = str(text).casefold()
            relationship_terms = sorted(
                {term for term in _RELATIONSHIP_TERMS if re.search(rf"\b{re.escape(term)}\b", lower_text)}
            )
            evidence = sorted(set(supplier_facts + [str(fact_id)]))
            signal_id = add_signal(
                con,
                signal_type="DECLARED_INTEREST_PSC_SUPPLIER_LINK",
                score=0.84,
                summary=(
                    f"A published declaration of interest names {psc_name}, and Companies House "
                    f"PSC data links that name to supplier {company_name}."
                ),
                subject=str(declaring_person_id),
                object_=company_key,
                evidence=evidence,
                metadata=common_metadata
                | {
                    "declaring_person_entity_id": str(declaring_person_id),
                    "relationship_terms_present": relationship_terms,
                    "match_method": "psc_name_exact_phrase_in_declared_interest",
                },
            )
            put_review(
                con,
                item_type="SIGNAL_REVIEW",
                score=0.84,
                summary=f"Verify declared-interest / PSC supplier link for {company_name}",
                subject=str(declaring_person_id),
                object_=company_key,
                evidence=evidence,
                metadata={
                    "signal_id": signal_id,
                    "reason": "declared_interest_names_companies_house_psc",
                    "companies_house_source_url": str(source_url),
                    "relationship_terms_present": relationship_terms,
                },
            )
            stats["psc_declared_interest_candidates"] += 1

    return stats
