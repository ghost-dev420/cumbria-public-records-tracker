from __future__ import annotations

import csv
import hashlib
import io
import re
from decimal import Decimal, InvalidOperation

import duckdb
from dateutil import parser as date_parser

from ..models import Record
from ..resolution import add_identifier
from ..structured import add_fact, upsert_entity
from ..supplier_hygiene import basic_org_key, split_payment_channel


_ALIASES = {
    "reference": {
        "contract reference", "contract ref", "contract reference number", "reference",
        "reference no", "number", "contract number",
    },
    "title": {
        "contract name", "contract title", "title", "short description",
    },
    "description": {
        "description", "contract description", "scope", "scope of contract",
    },
    "supplier": {
        "supplier", "supplier name", "vendor", "awarded supplier", "contractor",
    },
    "company_number": {
        "company number", "company no", "company reg number",
        "company registration number", "companies house number",
    },
    "method": {
        "procurement method", "procurement route", "process used", "procurement regime",
    },
    "start": {
        "start date", "contract start date", "starts", "commencement date",
    },
    "end": {
        "end date", "contract end date", "ends", "expiry date",
    },
    "review": {
        "contract review date", "review date", "renewal extension end date",
        "renewal/extension end date",
    },
    "value_ex_vat": {
        "contract value ex vat", "contract value excluding vat",
        "contract value (ex vat)", "total contract value ex vat",
        "total contract value (ex vat)", "awarded value ex vat",
    },
    "value_inc_vat": {
        "contract value incl vat", "contract value including vat",
        "contract value (incl vat)", "total contract value incl vat",
        "total contract value (incl vat)",
    },
}

_GENERIC_SUPPLIERS = {
    "various",
    "multiple award",
    "multiple suppliers",
    "framework suppliers",
    "n a",
    "na",
    "not applicable",
}


def _normalise_header(value: str) -> str:
    value = str(value or "").casefold().replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _decode(body: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


def _rows(body: bytes):
    text = _decode(body)
    if not text.strip():
        return
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
    aliases = {_normalise_header(item) for item in _ALIASES[logical_name]}
    for key, value in row.items():
        if _normalise_header(key) in aliases:
            return str(value or "").strip()
    return ""


def _date(value: str) -> str | None:
    value = str(value or "").strip()
    if not value:
        return None
    try:
        return date_parser.parse(value, dayfirst=True, fuzzy=False).date().isoformat()
    except (ValueError, OverflowError, TypeError):
        return value


def _money(value: str) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    cleaned = raw.replace("£", "").replace(",", "").strip()
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


def _suppliers(value: str) -> list[str]:
    raw = str(value or "").replace("\r", "\n")
    parts = re.split(r"[\n;]+", raw)
    output: list[str] = []
    seen: set[str] = set()
    for part in parts:
        name, _ = split_payment_channel(part)
        name = " ".join(name.split()).strip(" -–—")
        key = basic_org_key(name)
        if not name or key in _GENERIC_SUPPLIERS or key in seen:
            continue
        seen.add(key)
        output.append(name)
    return output


def _buyer_entity(con: duckdb.DuckDBPyConnection, source: dict) -> str | None:
    organisation_id = str(
        source.get("contract_buyer_organisation_id")
        or next(iter(source.get("organisation_ids") or []), "")
    ).strip()
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
        metadata={
            "organisation_id": organisation_id,
            "source_system": source.get("name", source.get("id", "contract register")),
        },
    )


def _looks_like_contract_register(row: dict[str, str]) -> bool:
    headers = {_normalise_header(key) for key in row}
    title_aliases = {_normalise_header(item) for item in _ALIASES["title"]}
    supplier_aliases = {_normalise_header(item) for item in _ALIASES["supplier"]}
    start_aliases = {_normalise_header(item) for item in _ALIASES["start"]}
    end_aliases = {_normalise_header(item) for item in _ALIASES["end"]}
    return bool(headers & title_aliases) and bool(headers & supplier_aliases) and bool(
        headers & (start_aliases | end_aliases)
    )


def extract_contract_register_csv(
    *,
    con: duckdb.DuckDBPyConnection,
    record: Record,
    document_id: str,
    snapshot_id: str,
    source: dict,
) -> int:
    ctype = record.content_type.casefold()
    if "csv" not in ctype and not record.url.casefold().split("?", 1)[0].endswith(".csv"):
        return 0

    parsed_rows = list(_rows(record.body) or [])
    if not parsed_rows or not _looks_like_contract_register(parsed_rows[0][1]):
        return 0

    buyer = _buyer_entity(con, source)
    source_name = str(source.get("name") or source.get("id") or "Council contract register")
    namespace = f"contract_register:{source.get('id', 'council')}"
    supplier_namespace = f"contract_register_supplier:{source.get('id', 'council')}"
    count = 0

    for row_number, row in parsed_rows:
        reference = _field(row, "reference")
        title = _field(row, "title")
        description = _field(row, "description")
        supplier_names = _suppliers(_field(row, "supplier"))
        if not title and not supplier_names:
            continue

        start = _date(_field(row, "start"))
        end = _date(_field(row, "end"))
        review = _date(_field(row, "review"))
        method = _field(row, "method") or None
        value_ex_vat = _money(_field(row, "value_ex_vat"))
        value_inc_vat = _money(_field(row, "value_inc_vat"))
        company_number = re.sub(r"\s+", "", _field(row, "company_number")) or None

        if reference:
            contract_key = reference
        else:
            identity = "\0".join([title, start or "", end or "", "|".join(supplier_names)])
            contract_key = "row-" + hashlib.sha256(identity.encode()).hexdigest()[:20]

        contract = upsert_entity(
            con,
            entity_type="CONTRACT",
            name=contract_key,
            namespace=namespace,
            metadata={
                "source_system": source_name,
                "contract_reference": reference or None,
                "title": title or None,
                "procurement_method": method,
                "source_url": record.url,
            },
        )
        locator = f"CSV row {row_number}"

        if title:
            add_fact(
                con,
                document_id=document_id,
                snapshot_id=snapshot_id,
                fact_type="CONTRACT_TITLE",
                predicate="HAS_TITLE",
                evidence_class=record.evidence_class.value,
                subject_entity_id=contract,
                value_text=title,
                locator=locator,
            )
            count += 1
        if description:
            add_fact(
                con,
                document_id=document_id,
                snapshot_id=snapshot_id,
                fact_type="CONTRACT_DESCRIPTION",
                predicate="HAS_DESCRIPTION",
                evidence_class=record.evidence_class.value,
                subject_entity_id=contract,
                value_text=description,
                locator=locator,
            )
            count += 1
        if buyer:
            add_fact(
                con,
                document_id=document_id,
                snapshot_id=snapshot_id,
                fact_type="CONTRACT_BUYER",
                predicate="BUYER_OF_CONTRACT",
                evidence_class=record.evidence_class.value,
                subject_entity_id=buyer,
                object_entity_id=contract,
                locator=locator,
            )
            count += 1

        supplier_entities: list[str] = []
        for supplier_name in supplier_names:
            supplier = upsert_entity(
                con,
                entity_type="SUPPLIER",
                name=supplier_name,
                namespace=supplier_namespace,
                metadata={"source_system": source_name},
            )
            supplier_entities.append(supplier)
            add_fact(
                con,
                document_id=document_id,
                snapshot_id=snapshot_id,
                fact_type="CONTRACT_SUPPLIER",
                predicate="SUPPLIER_TO_CONTRACT",
                evidence_class=record.evidence_class.value,
                subject_entity_id=supplier,
                object_entity_id=contract,
                locator=locator,
            )
            count += 1

        if company_number and len(supplier_entities) == 1:
            add_identifier(
                con,
                entity_id=supplier_entities[0],
                scheme="GB-COH",
                identifier=company_number,
                source=source_name,
            )

        if value_ex_vat is not None:
            add_fact(
                con,
                document_id=document_id,
                snapshot_id=snapshot_id,
                fact_type="CONTRACT_VALUE",
                predicate="HAS_VALUE",
                evidence_class=record.evidence_class.value,
                subject_entity_id=contract,
                value_text=value_ex_vat,
                locator=locator,
                metadata={"currency": "GBP", "vat_basis": "ex_vat"},
            )
            count += 1
        elif value_inc_vat is not None:
            add_fact(
                con,
                document_id=document_id,
                snapshot_id=snapshot_id,
                fact_type="CONTRACT_VALUE_INCLUDING_VAT",
                predicate="HAS_VALUE_INCLUDING_VAT",
                evidence_class=record.evidence_class.value,
                subject_entity_id=contract,
                value_text=value_inc_vat,
                locator=locator,
                metadata={"currency": "GBP", "vat_basis": "including_vat"},
            )
            count += 1

        if start:
            add_fact(
                con,
                document_id=document_id,
                snapshot_id=snapshot_id,
                fact_type="CONTRACT_PERIOD",
                predicate="STARTS_ON",
                evidence_class=record.evidence_class.value,
                subject_entity_id=contract,
                value_text=start,
                locator=locator,
                metadata={"period_source": "council_contract_register"},
            )
            count += 1
        if end:
            add_fact(
                con,
                document_id=document_id,
                snapshot_id=snapshot_id,
                fact_type="CONTRACT_PERIOD",
                predicate="ENDS_ON",
                evidence_class=record.evidence_class.value,
                subject_entity_id=contract,
                value_text=end,
                locator=locator,
                metadata={"period_source": "council_contract_register"},
            )
            count += 1
        if review:
            add_fact(
                con,
                document_id=document_id,
                snapshot_id=snapshot_id,
                fact_type="CONTRACT_REVIEW_DATE",
                predicate="REVIEW_ON",
                evidence_class=record.evidence_class.value,
                subject_entity_id=contract,
                value_text=review,
                locator=locator,
            )
            count += 1
        if method:
            add_fact(
                con,
                document_id=document_id,
                snapshot_id=snapshot_id,
                fact_type="PROCUREMENT_METHOD",
                predicate="HAS_PROCUREMENT_METHOD",
                evidence_class=record.evidence_class.value,
                subject_entity_id=contract,
                value_text=method,
                locator=locator,
            )
            count += 1

    return count
