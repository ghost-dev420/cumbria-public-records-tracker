from __future__ import annotations

import csv
import io
import re
from decimal import Decimal, InvalidOperation
from typing import Iterable

import duckdb
from dateutil import parser as date_parser

from ..models import Record
from ..structured import add_fact, upsert_entity


_HEADER_ALIASES = {
    "supplier": {
        "supplier", "suppliername", "suppliervendorname", "vendor", "vendorname",
        "payee", "payee name", "creditor", "creditorname",
    },
    "amount": {
        "amount", "transactionamount", "netamount", "paymentamount", "value",
        "amountexcludingvat", "amountgbp", "grossamount",
    },
    "date": {
        "date", "paymentdate", "transactiondate", "invoicedate", "paiddate",
    },
    "description": {
        "description", "transactiondescription", "narrative", "purpose",
        "expensetype", "expendituretype",
    },
    "department": {
        "department", "directorate", "service", "servicearea", "costcentre",
        "costcenter", "costcentrename",
    },
    "reference": {
        "transactionnumber", "transactionid", "documentnumber", "reference",
        "invoicenumber", "paymentreference",
    },
}


def _normalise_header(value: str) -> str:
    value = value.casefold().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "", value)


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


def _field(row: dict[str, str], logical_name: str) -> str:
    aliases = {_normalise_header(value) for value in _HEADER_ALIASES[logical_name]}
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


def _is_supplier_file(record: Record) -> bool:
    text = " ".join(
        [
            record.title,
            record.url,
            str(record.metadata.get("anchor_text") or ""),
        ]
    ).casefold()
    if any(term in text for term in ("private home", "support related", "support-related")):
        return False
    return "trade supplier" in text or "trade-supplier" in text


def _payer_entity(con: duckdb.DuckDBPyConnection, source: dict) -> str | None:
    organisation_id = source.get("payment_payer_organisation_id")
    if not organisation_id:
        organisation_ids = list(source.get("organisation_ids") or [])
        organisation_id = organisation_ids[0] if organisation_ids else None
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
    is_csv = "csv" in record.content_type.casefold() or record.url.casefold().endswith(".csv")
    if not is_csv or not _is_supplier_file(record):
        return 0

    payer = _payer_entity(con, source)
    count = 0
    for row_number, row in _dict_rows(record.body):
        supplier_name = _field(row, "supplier")
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
        add_fact(
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
            },
        )
        count += 1
    return count
