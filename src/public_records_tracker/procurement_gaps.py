from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation

import duckdb

from .analysis import add_signal
from .analysis_quality import prepare_analysis_resolution
from .resolution import ensure_resolution_schema, put_review
from .supplier_hygiene import basic_org_key, classify_noncommercial_payee


DISCOVERY_THRESHOLD_GBP = Decimal("30000")
CAVEATS = [
    "The tracker does not necessarily contain every contract, framework, purchase order, variation, direct award or procurement route.",
    "Some published payments do not require a separately discoverable contract record in the sources currently indexed.",
    "The £30,000 threshold used here is a noise-reduction setting for discovery, not a legal or procurement threshold.",
    "Payment credits are netted against positive payments before the discovery threshold is applied.",
    "Contract links are compared within the paying/buying authority where that authority is known.",
    "This is an internal review lead only and is not evidence of non-compliance or improper conduct.",
]

_LEGACY_LOCAL_AUTHORITY_TYPES = {
    "district_council",
    "county_council",
    "borough_council",
    "city_council",
}
_AUTHORITY_NAME_SUFFIXES = (
    " borough council",
    " district council",
    " county council",
    " city council",
    " town council",
    " parish council",
    " council",
)


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


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


def _strip_authority_suffix(value: str) -> str:
    key = basic_org_key(value)
    for suffix in _AUTHORITY_NAME_SUFFIXES:
        if key.endswith(suffix):
            return key[: -len(suffix)].strip()
    return key


def _configured_public_authority_aliases(
    con: duckdb.DuckDBPyConnection,
) -> set[str]:
    """Build conservative payee aliases from configured council identities.

    Full configured council names are always recognised.  For legacy district /
    county authorities we also recognise the bare locality name because historic
    transparency exports often shorten, for example, "Allerdale Borough Council"
    to just "ALLERDALE".  Bare locality aliases are not generated for current
    town/parish councils to avoid over-classifying ordinary businesses.
    """
    aliases: set[str] = set()
    rows = con.execute(
        "SELECT name,organisation_type,status FROM organisations"
    ).fetchall()
    for name, organisation_type, status in rows:
        key = basic_org_key(str(name))
        if not key:
            continue
        org_type = str(organisation_type or "").casefold()
        if "council" not in org_type and org_type not in {
            "unitary_authority",
            "combined_authority",
        }:
            continue
        aliases.add(key)
        if str(status or "").casefold() == "legacy" and org_type in _LEGACY_LOCAL_AUTHORITY_TYPES:
            base = _strip_authority_suffix(str(name))
            if len(base) >= 5:
                aliases.add(base)
    return aliases


def detect_unmatched_payment_streams(con: duckdb.DuckDBPyConnection) -> int:
    canonical = _canonical_map(con)
    public_authority_aliases = _configured_public_authority_aliases(con)
    payments: dict[tuple[str | None, str], list[tuple[str, Decimal]]] = defaultdict(list)
    for fact_id, payer_id, supplier_id, value_text in con.execute(
        """SELECT fact_id,subject_entity_id,object_entity_id,value_text
           FROM latest_facts
           WHERE predicate='PAYMENT_TO_SUPPLIER'
             AND object_entity_id IS NOT NULL"""
    ).fetchall():
        amount = _decimal(value_text)
        if amount is None or amount == 0:
            continue
        payer_key = (
            canonical.get(str(payer_id), str(payer_id)) if payer_id is not None else None
        )
        supplier_key = canonical.get(str(supplier_id), str(supplier_id))
        payments[(payer_key, supplier_key)].append((str(fact_id), amount))

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

    contract_scopes: set[tuple[str | None, str]] = set()
    for supplier_id, contract_id in con.execute(
        """SELECT subject_entity_id,object_entity_id
           FROM latest_facts
           WHERE predicate='SUPPLIER_TO_CONTRACT'
             AND subject_entity_id IS NOT NULL
             AND object_entity_id IS NOT NULL"""
    ).fetchall():
        supplier_key = canonical.get(str(supplier_id), str(supplier_id))
        buyers = contract_buyers.get(str(contract_id)) or {None}
        for buyer_key in buyers:
            contract_scopes.add((buyer_key, supplier_key))

    count = 0
    for (payer_id, supplier_id), rows in payments.items():
        if (payer_id, supplier_id) in contract_scopes:
            continue
        if payer_id is not None and payer_id == supplier_id:
            continue

        total = sum((amount for _, amount in rows), Decimal("0"))
        if total < DISCOVERY_THRESHOLD_GBP:
            continue

        name = _entity_name(con, supplier_id) or supplier_id
        suppressed_reason = classify_noncommercial_payee(name)
        if suppressed_reason is None and basic_org_key(name) in public_authority_aliases:
            suppressed_reason = "configured_public_authority"
        if suppressed_reason is not None:
            continue

        positive_total = sum(
            (amount for _, amount in rows if amount > 0), Decimal("0")
        )
        credit_total = sum(
            (amount for _, amount in rows if amount < 0), Decimal("0")
        )
        evidence = sorted({fact_id for fact_id, _ in rows})
        payer_name = _entity_name(con, payer_id)
        scope_text = f" from {payer_name}" if payer_name else ""
        signal_id = add_signal(
            con,
            signal_type="PAYMENT_STREAM_WITHOUT_TRACKED_PROCUREMENT_LINK",
            score=0.30,
            summary=(
                f"Published net payments{scope_text} linked to {name} total £{total:,.2f}, "
                "while the tracker currently has no buyer-scoped supplier-to-contract link "
                "for that payment stream."
            ),
            subject=supplier_id,
            object_=payer_id,
            evidence=evidence,
            metadata={
                "payment_total_gbp": float(total),
                "net_payment_total_gbp": float(total),
                "gross_positive_payments_gbp": float(positive_total),
                "credit_total_gbp": float(credit_total),
                "payment_record_count": len(rows),
                "credit_record_count": sum(1 for _, amount in rows if amount < 0),
                "payer_entity_id": payer_id,
                "buyer_scoped": payer_id is not None,
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
            object_=payer_id,
            evidence=evidence,
            metadata={
                "signal_id": signal_id,
                "reason": "large_net_payment_stream_without_buyer_scoped_contract_link",
                "payer_entity_id": payer_id,
                "caveats": CAVEATS,
            },
        )
        count += 1
    return count


def run_procurement_gap_detectors(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    return {"unmatched_payment_stream_review": detect_unmatched_payment_streams(con)}
