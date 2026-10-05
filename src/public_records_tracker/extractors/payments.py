from __future__ import annotations

import csv
import io
import re
from decimal import Decimal, InvalidOperation
from typing import Iterable
from urllib.parse import urlsplit

import duckdb
from dateutil import parser as date_parser

from ..models import Record
from ..structured import add_fact, upsert_entity
from ..supplier_hygiene import split_payment_channel


_HEADER_ALIASES = {
    "supplier": {
        "supplier", "suppliername", "suppliervendorname", "vendor", "vendorname",
        "payee", "payee name", "creditor", "creditorname", "supplier/payee",
        "supplier/payee name", "supplier or payee", "supplier or payee name",
    },
    "amount": {
        "amount", "transactionamount", "netamount", "paymentamount", "value",
        "amountexcludingvat", "amountgbp", "grossamount", "invoiceamount",
        "net value", "gross value",
    },
    "date": {
        "date", "paymentdate", "transactiondate", "invoicedate", "paiddate",
        "payment date", "transaction date", "invoice date",
    },
    "description": {
        "description", "transactiondescription", "narrative", "purpose",
        "expensetype", "expendituretype", "transaction description",
    },
    "department": {
        "department", "directorate", "service", "servicearea", "costcentre",
        "costcenter", "costcentrename", "service area", "cost centre",
    },
    "reference": {
        "transactionnumber", "transactionid", "documentnumber", "reference",
        "invoicenumber", "paymentreference", "transaction number", "invoice number",
    },
}

_DEFAULT_ALLOW_TERMS = (
    "trade supplier",
    "trade-supplier",
    "spending over",
    "spend over",
    "payments over",
    "expenditure over",
    "supplier payments",
)
_DEFAULT_DENY_TERMS = (
    "private home",
    "private-home",
    "support related",
    "support-related",
    "supported individual",
    "supported individuals",
    "personal payment",
)


def _normalise_header(value: str) -> str:
    value = value.casefold().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "", value)


def _normalised_aliases(logical_name: str) -> set[str]:
    return {_normalise_header(value) for value in _HEADER_ALIASES[logical_name]}


def _decode_csv(body: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


def _dict_rows(body: bytes) -> Iterable[tuple[int, dict[str, str]]]:
    text = _decode_csv(body)
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    for row_number, row in enumerate(reader, start=2):
        clean = {
            str(key or "").strip(): str(value or "").strip()
            for key, value in row.items()
            if key is not None
        }
        if any(clean.values()):
            yield row_number, clean


def _has_payment_headers(body: bytes) -> bool:
    """Require both a supplier/payee column and a monetary amount column."""
    try:
        _, first = next(iter(_dict_rows(body)))
    except (StopIteration, csv.Error, UnicodeError):
        return False
    headers = {_normalise_header(key) for key in first}
    return bool(headers & _normalised_aliases("supplier")) and bool(
        headers & _normalised_aliases("amount")
    )


def _field(row: dict[str, str], logical_name: str) -> str:
    aliases = _normalised_aliases(logical_name)
    for key, value in row.items():
        if _normalise_header(key) in aliases:
            return value.strip()
    return ""


def _amount(value: str) -> str | None:
    cleaned = value.strip().replace("£", "").replace(",", "")
    if not cleaned:
        return None
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1]
    cleaned = re.sub(r"\s+", "", cleaned)
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    if negative:
        amount = -amount
    return format(amount.quantize(Decimal("0.01")), "f")


def _date(value: str) -> str | None:
    if not value.strip():
        return None
    try:
        return date_parser.parse(value, dayfirst=True, fuzzy=False).date().isoformat()
    except (ValueError, OverflowError):
        return value.strip()


def _record_text(record: Record) -> str:
    return " ".join(
        [
            record.title,
            record.url,
            str(record.metadata.get("anchor_text") or ""),
            str(record.metadata.get("discovered_from") or ""),
        ]
    ).casefold()


def _is_supplier_file(record: Record, source: dict, *, has_headers: bool) -> bool:
    """Select business-supplier CSVs while explicitly excluding sensitive payment series."""
    text = _record_text(record)
    deny_terms = tuple(
        str(item).casefold()
        for item in source.get("payment_file_deny_terms", _DEFAULT_DENY_TERMS)
    )
    if any(term and term in text for term in deny_terms):
        return False

    allow_terms = tuple(
        str(item).casefold()
        for item in source.get("payment_file_allow_terms", _DEFAULT_ALLOW_TERMS)
    )
    named_as_supplier_file = any(term and term in text for term in allow_terms)
    if named_as_supplier_file:
        return has_headers

    # Some councils publish downloads behind generic CDN/object-store URLs whose
    # final URL and MIME type lose the original .csv filename. Header-based
    # detection is therefore opt-in per known transparency source.
    return bool(source.get("allow_payment_csv_by_header", False) and has_headers)


def _is_csv_candidate(record: Record, *, has_headers: bool, source: dict) -> bool:
    content_type = record.content_type.casefold()
    path = urlsplit(record.url).path.casefold()
    metadata_name = " ".join(
        [record.title, str(record.metadata.get("anchor_text") or "")]
    ).casefold()
    explicit_csv = (
        "csv" in content_type
        or path.endswith(".csv")
        or ".csv" in metadata_name
    )
    return explicit_csv or bool(source.get("allow_payment_csv_by_header", False) and has_headers)


def _payer_organisation_id(source: dict, record: Record) -> str | None:
    text = _record_text(record)
    for rule in source.get("payment_payer_rules") or []:
        if not isinstance(rule, dict):
            continue
        organisation_id = str(rule.get("organisation_id") or "").strip()
        terms = [str(term).casefold() for term in rule.get("terms") or []]
        if organisation_id and any(term and term in text for term in terms):
            return organisation_id

    organisation_id = source.get("payment_payer_organisation_id")
    if organisation_id:
        return str(organisation_id)
    organisation_ids = list(source.get("organisation_ids") or [])
    return str(organisation_ids[0]) if organisation_ids else None


def _payer_entity(
    con: duckdb.DuckDBPyConnection, source: dict, record: Record
) -> str | None:
    organisation_id = _payer_organisation_id(source, record)
    if not organisation_id:
        return None
    row = con.execute(
        "SELECT name FROM organisations WHERE organisation_id=?", [organisation_id]
    ).fetchone()
    if not row:
        return None
    return upsert_entity(
        con,
        entity_type="COUNCIL",
        name=str(row[0]),
        namespace=f"organisation:{organisation_id}",
        metadata={"organisation_id": organisation_id, "source_system": "council spending"},
    )


def extract_payments(
    *,
    con: duckdb.DuckDBPyConnection,
    record: Record,
    document_id: str,
    snapshot_id: str,
    source: dict,
) -> int:
    has_headers = _has_payment_headers(record.body)
    if not _is_csv_candidate(record, has_headers=has_headers, source=source):
        return 0
    if not _is_supplier_file(record, source, has_headers=has_headers):
        return 0

    rows = list(_dict_rows(record.body))
    if not rows:
        return 0

    # Payment fact identity includes the supplier entity. Supplier hygiene can
    # therefore produce a new fact ID when an existing snapshot is re-extracted.
    # Do not DELETE the old indexed fact rows: repeated bulk deletes have caused
    # DuckDB index invalidation on Android/aarch64. Instead add the corrected
    # fact, then retire any older fact occupying the same source-row slot by
    # changing only its unindexed predicate. This preserves provenance without
    # allowing stale supplier identities to remain active or double-count spend.

    payer = _payer_entity(con, source, record)
    count = 0
    for row_number, row in rows:
        raw_supplier_name = _field(row, "supplier")
        supplier_name, payment_channel = split_payment_channel(raw_supplier_name)
        amount = _amount(_field(row, "amount"))
        if not supplier_name or amount is None:
            continue
        supplier = upsert_entity(
            con,
            entity_type="SUPPLIER",
            name=supplier_name,
            namespace=f"payments:{source['id']}",
            metadata={"source_system": source.get("name", source["id"])},
        )
        payment_date = _date(_field(row, "date"))
        description = _field(row, "description") or None
        department = _field(row, "department") or None
        reference = _field(row, "reference") or None
        locator = f"CSV row {row_number}"
        fact_id = add_fact(
            con,
            document_id=document_id,
            snapshot_id=snapshot_id,
            fact_type="PAYMENT",
            predicate="PAYMENT_TO_SUPPLIER",
            evidence_class=record.evidence_class.value,
            subject_entity_id=payer,
            object_entity_id=supplier,
            value_text=amount,
            locator=locator,
            metadata={
                "currency": "GBP",
                "payment_date": payment_date,
                "description": description,
                "department": department,
                "reference": reference,
                "source_row": row_number,
                "raw_supplier_name": raw_supplier_name or supplier_name,
                "payment_channel": payment_channel,
            },
        )
        con.execute(
            """UPDATE facts
               SET predicate='SUPERSEDED_PAYMENT_TO_SUPPLIER'
               WHERE document_id=? AND snapshot_id=?
                 AND fact_type='PAYMENT'
                 AND predicate='PAYMENT_TO_SUPPLIER'
                 AND locator=? AND fact_id<>?""",
            [document_id, snapshot_id, locator, fact_id],
        )
        count += 1
    return count
