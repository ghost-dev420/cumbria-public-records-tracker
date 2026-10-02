from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation

import duckdb

from .analysis import add_signal
from .resolution import ensure_resolution_schema, put_review


DISCOVERY_THRESHOLD_GBP = Decimal("30000")
CAVEATS = [
    "The tracker does not necessarily contain every contract, framework, purchase order, variation, direct award or procurement route.",
    "Some published payments do not require a separately discoverable contract record in the sources currently indexed.",
    "The £30,000 threshold used here is a noise-reduction setting for discovery, not a legal or procurement threshold.",
    "This is an internal review lead only and is not evidence of non-compliance or improper conduct.",
]


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _canonical_map(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    ensure_resolution_schema(con)
    return {
        str(entity_id): str(canonical_id)
        for entity_id, canonical_id in con.execute(
            "SELECT entity_id,canonical_entity_id FROM resolved_entity_members"
        ).fetchall()
    }


def _supplier_name(con: duckdb.DuckDBPyConnection, entity_id: str) -> str:
    row = con.execute(
        "SELECT canonical_name FROM entities WHERE entity_id=?", [entity_id]
    ).fetchone()
    return str(row[0]) if row else entity_id


def detect_unmatched_payment_streams(con: duckdb.DuckDBPyConnection) -> int:
    canonical = _canonical_map(con)
    payments: dict[str, list[tuple[str, Decimal]]] = defaultdict(list)
    for fact_id, supplier_id, value_text in con.execute(
        """SELECT fact_id,object_entity_id,value_text
           FROM latest_facts
           WHERE predicate='PAYMENT_TO_SUPPLIER'
             AND object_entity_id IS NOT NULL"""
    ).fetchall():
        amount = _decimal(value_text)
        if amount is None or amount <= 0:
            continue
        key = canonical.get(str(supplier_id), str(supplier_id))
        payments[key].append((str(fact_id), amount))

    suppliers_with_contracts: set[str] = set()
    for supplier_id, in con.execute(
        """SELECT DISTINCT subject_entity_id
           FROM latest_facts
           WHERE predicate='SUPPLIER_TO_CONTRACT'
             AND subject_entity_id IS NOT NULL"""
    ).fetchall():
        suppliers_with_contracts.add(canonical.get(str(supplier_id), str(supplier_id)))

    count = 0
    for supplier_id, rows in payments.items():
        if supplier_id in suppliers_with_contracts:
            continue
        total = sum((amount for _, amount in rows), Decimal("0"))
        if total < DISCOVERY_THRESHOLD_GBP:
            continue
        evidence = sorted({fact_id for fact_id, _ in rows})
        name = _supplier_name(con, supplier_id)
        signal_id = add_signal(
            con,
            signal_type="PAYMENT_STREAM_WITHOUT_TRACKED_PROCUREMENT_LINK",
            score=0.30,
            summary=(
                f"Published payments linked to {name} total £{total:,.2f}, while the tracker "
                "currently has no resolved supplier-to-contract link for that entity."
            ),
            subject=supplier_id,
            object_=None,
            evidence=evidence,
            metadata={
                "payment_total_gbp": float(total),
                "payment_record_count": len(rows),
                "discovery_threshold_gbp": float(DISCOVERY_THRESHOLD_GBP),
                "threshold_is_legal_threshold": False,
                "caveats": CAVEATS,
                "interpretation": "source_discovery_review_not_a_finding",
            },
        )
        put_review(
            con,
            item_type="SOURCE_DISCOVERY_REVIEW",
            score=0.30,
            summary=f"Look for additional procurement records associated with {name}",
            subject=supplier_id,
            object_=None,
            evidence=evidence,
            metadata={
                "signal_id": signal_id,
                "reason": "large_payment_stream_without_current_contract_link",
                "caveats": CAVEATS,
            },
        )
        count += 1
    return count


def run_procurement_gap_detectors(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    return {"unmatched_payment_stream_review": detect_unmatched_payment_streams(con)}
