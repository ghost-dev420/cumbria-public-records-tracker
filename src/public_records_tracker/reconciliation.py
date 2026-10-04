from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

import duckdb
from dateutil import parser as date_parser

from .analysis import add_signal
from .analysis_quality import prepare_analysis_resolution
from .resolution import ensure_resolution_schema, put_review


CAVEATS = [
    "The tracker may not contain every contract, framework, variation or extension.",
    "Published payment totals can include VAT, credits, timing differences or other legitimate streams.",
    "Credits are netted for value comparison and non-positive rows are not treated as out-of-period payments.",
    "Payments are compared only with contracts for the same paying/buying authority where that authority is known.",
    "A mismatch is a review signal only and is not evidence of improper conduct.",
]


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date_parser.parse(str(value), fuzzy=False).date()
    except (ValueError, OverflowError, TypeError):
        return None


def _metadata(value: str | None) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _canonical_map(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    ensure_resolution_schema(con)
    prepare_analysis_resolution(con)
    return {
        str(entity_id): str(canonical_id)
        for entity_id, canonical_id in con.execute(
            "SELECT entity_id,canonical_entity_id FROM resolved_entity_members"
        ).fetchall()
    }


def _entity_name(con: duckdb.DuckDBPyConnection, entity_id: str | None) -> str | None:
    if entity_id is None:
        return None
    row = con.execute(
        "SELECT canonical_name FROM entities WHERE entity_id=?", [entity_id]
    ).fetchone()
    return str(row[0]) if row else entity_id


def detect_payment_contract_value_mismatch(con: duckdb.DuckDBPyConnection) -> int:
    canonical = _canonical_map(con)

    payments: dict[tuple[str | None, str], list[dict]] = defaultdict(list)
    for fact_id, payer_id, supplier_id, value_text, metadata_json in con.execute(
        """SELECT fact_id,subject_entity_id,object_entity_id,value_text,metadata_json
           FROM latest_facts
           WHERE predicate='PAYMENT_TO_SUPPLIER'
             AND object_entity_id IS NOT NULL"""
    ).fetchall():
        amount = _decimal(value_text)
        if amount is None:
            continue
        payer_key = (
            canonical.get(str(payer_id), str(payer_id)) if payer_id is not None else None
        )
        supplier_key = canonical.get(str(supplier_id), str(supplier_id))
        meta = _metadata(metadata_json)
        payments[(payer_key, supplier_key)].append(
            {
                "fact_id": str(fact_id),
                "amount": amount,
                "date": _date(meta.get("payment_date")),
            }
        )

    contract_buyers: dict[str, set[str]] = defaultdict(set)
    for buyer_id, contract_id in con.execute(
        """SELECT subject_entity_id,object_entity_id
           FROM latest_facts
           WHERE predicate='BUYER_OF_CONTRACT'
             AND subject_entity_id IS NOT NULL
             AND object_entity_id IS NOT NULL"""
    ).fetchall():
        contract_buyers[str(contract_id)].add(
            canonical.get(str(buyer_id), str(buyer_id))
        )

    scoped_contracts: dict[
        tuple[str | None, str], dict[str, list[str]]
    ] = defaultdict(lambda: defaultdict(list))
    for fact_id, supplier_id, contract_id in con.execute(
        """SELECT fact_id,subject_entity_id,object_entity_id
           FROM latest_facts
           WHERE predicate='SUPPLIER_TO_CONTRACT'
             AND subject_entity_id IS NOT NULL
             AND object_entity_id IS NOT NULL"""
    ).fetchall():
        supplier_key = canonical.get(str(supplier_id), str(supplier_id))
        contract_key = str(contract_id)
        buyers = contract_buyers.get(contract_key) or {None}
        for buyer_key in buyers:
            scoped_contracts[(buyer_key, supplier_key)][contract_key].append(
                str(fact_id)
            )

    values: dict[str, list[tuple[str, Decimal]]] = defaultdict(list)
    for fact_id, contract_id, value_text, metadata_json in con.execute(
        """SELECT fact_id,subject_entity_id,value_text,metadata_json
           FROM latest_facts
           WHERE predicate='HAS_VALUE'
             AND subject_entity_id IS NOT NULL"""
    ).fetchall():
        meta = _metadata(metadata_json)
        currency = str(meta.get("currency") or "GBP").upper()
        amount = _decimal(value_text)
        if currency == "GBP" and amount is not None and amount >= 0:
            values[str(contract_id)].append((str(fact_id), amount))

    periods: dict[str, dict[str, list[tuple[str, date]]]] = defaultdict(
        lambda: {"start": [], "end": []}
    )
    for fact_id, contract_id, predicate, value_text in con.execute(
        """SELECT fact_id,subject_entity_id,predicate,value_text
           FROM latest_facts
           WHERE predicate IN ('STARTS_ON','ENDS_ON')
             AND subject_entity_id IS NOT NULL"""
    ).fetchall():
        parsed = _date(value_text)
        if parsed is None:
            continue
        key = "start" if predicate == "STARTS_ON" else "end"
        periods[str(contract_id)][key].append((str(fact_id), parsed))

    count = 0
    for (payer_id, supplier_id), supplier_payments in payments.items():
        contracts = scoped_contracts.get((payer_id, supplier_id))
        if not contracts:
            continue

        tracked_values: list[Decimal] = []
        value_evidence: list[str] = []
        relation_evidence: list[str] = []
        complete_periods: list[tuple[date, date, list[str]]] = []
        for contract_id, relation_facts in contracts.items():
            relation_evidence.extend(relation_facts)
            contract_values = values.get(contract_id) or []
            if contract_values:
                # Multiple notices/releases can restate one award. Count the largest current
                # GBP value once per resolved contract and retain every value fact as evidence.
                tracked_values.append(max(amount for _, amount in contract_values))
                value_evidence.extend(fact_id for fact_id, _ in contract_values)
            starts = periods[contract_id]["start"]
            ends = periods[contract_id]["end"]
            if starts and ends:
                start_fact, start_date = min(starts, key=lambda item: item[1])
                end_fact, end_date = max(ends, key=lambda item: item[1])
                if start_date <= end_date:
                    complete_periods.append(
                        (start_date, end_date, [start_fact, end_fact, *relation_facts])
                    )

        payment_total = sum(
            (item["amount"] for item in supplier_payments), Decimal("0")
        )
        positive_total = sum(
            (item["amount"] for item in supplier_payments if item["amount"] > 0),
            Decimal("0"),
        )
        credit_total = sum(
            (item["amount"] for item in supplier_payments if item["amount"] < 0),
            Decimal("0"),
        )
        payment_evidence = [item["fact_id"] for item in supplier_payments]
        name = _entity_name(con, supplier_id) or supplier_id
        payer_name = _entity_name(con, payer_id)
        scope_text = f" from {payer_name}" if payer_name else ""

        if tracked_values:
            tracked_total = sum(tracked_values, Decimal("0"))
            difference = payment_total - tracked_total
            if (
                tracked_total > 0
                and payment_total > tracked_total * Decimal("1.10")
                and difference >= Decimal("1000")
            ):
                evidence = sorted(
                    set(payment_evidence + relation_evidence + value_evidence)
                )
                signal_id = add_signal(
                    con,
                    signal_type="PAYMENTS_EXCEED_TRACKED_CONTRACT_VALUE",
                    score=0.55,
                    summary=(
                        f"Published net payments{scope_text} linked to {name} total "
                        f"£{payment_total:,.2f}; buyer-scoped tracked GBP contract award "
                        f"values total £{tracked_total:,.2f}."
                    ),
                    subject=supplier_id,
                    object_=payer_id,
                    evidence=evidence,
                    metadata={
                        "payment_total_gbp": float(payment_total),
                        "net_payment_total_gbp": float(payment_total),
                        "gross_positive_payments_gbp": float(positive_total),
                        "credit_total_gbp": float(credit_total),
                        "tracked_contract_value_gbp": float(tracked_total),
                        "difference_gbp": float(difference),
                        "tracked_contract_count": len(contracts),
                        "payer_entity_id": payer_id,
                        "buyer_scoped": payer_id is not None,
                        "caveats": CAVEATS,
                        "interpretation": "review_required_not_a_finding",
                    },
                )
                put_review(
                    con,
                    item_type="SIGNAL_REVIEW",
                    score=0.55,
                    summary=f"Review payment / tracked contract value difference for {name}",
                    subject=supplier_id,
                    object_=payer_id,
                    evidence=evidence,
                    metadata={"signal_id": signal_id, "payer_entity_id": payer_id},
                )
                count += 1

        if complete_periods:
            outside: list[dict] = []
            period_evidence: list[str] = []
            for _, _, facts in complete_periods:
                period_evidence.extend(facts)
            for item in supplier_payments:
                # Credits/refunds after a contract period are not positive spend and should
                # not generate an out-of-period payment anomaly by themselves.
                if item["amount"] <= 0:
                    continue
                payment_date = item["date"]
                if payment_date is None:
                    continue
                if not any(start <= payment_date <= end for start, end, _ in complete_periods):
                    outside.append(item)
            if outside:
                evidence = sorted(
                    set(
                        [item["fact_id"] for item in outside]
                        + relation_evidence
                        + period_evidence
                    )
                )
                signal_id = add_signal(
                    con,
                    signal_type="PAYMENTS_OUTSIDE_TRACKED_CONTRACT_PERIODS",
                    score=0.45,
                    summary=(
                        f"{len(outside)} positive published payment record(s){scope_text} linked "
                        f"to {name} fall outside all complete buyer-scoped contract periods "
                        "currently tracked for that supplier."
                    ),
                    subject=supplier_id,
                    object_=payer_id,
                    evidence=evidence,
                    metadata={
                        "outside_payment_count": len(outside),
                        "complete_tracked_period_count": len(complete_periods),
                        "payer_entity_id": payer_id,
                        "buyer_scoped": payer_id is not None,
                        "credits_excluded_from_period_check": True,
                        "caveats": CAVEATS,
                        "interpretation": "review_required_not_a_finding",
                    },
                )
                put_review(
                    con,
                    item_type="SIGNAL_REVIEW",
                    score=0.45,
                    summary=f"Review payment dates against tracked contract periods for {name}",
                    subject=supplier_id,
                    object_=payer_id,
                    evidence=evidence,
                    metadata={"signal_id": signal_id, "payer_entity_id": payer_id},
                )
                count += 1
    return count


def run_reconciliation_detectors(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    return {
        "payment_contract_value_or_period_review": detect_payment_contract_value_mismatch(con)
    }
