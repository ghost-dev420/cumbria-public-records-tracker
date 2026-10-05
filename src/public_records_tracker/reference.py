from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import duckdb
import httpx
from bs4 import BeautifulSoup

from .resolution import (
    add_alias,
    add_identifier,
    ensure_resolution_schema,
    normalize_org_name,
    refresh_canonical_aliases,
    run_resolution,
)
from .structured import ensure_structured_schema, upsert_entity


USER_AGENT = (
    "CumbriaPublicRecordsEvidenceTracker/0.2 "
    "(public-interest registry matching; evidence-first; contact via project repository)"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS registry_records (
    registry_record_id VARCHAR PRIMARY KEY,
    registry VARCHAR NOT NULL,
    record_type VARCHAR NOT NULL,
    external_id VARCHAR NOT NULL,
    entity_id VARCHAR,
    canonical_name VARCHAR,
    source_url VARCHAR NOT NULL,
    dataset_date VARCHAR,
    metadata_json VARCHAR NOT NULL DEFAULT '{}',
    updated_at TIMESTAMP DEFAULT current_timestamp
);
CREATE INDEX IF NOT EXISTS registry_records_lookup_idx
    ON registry_records(registry, record_type, external_id);

CREATE TABLE IF NOT EXISTS registry_relationships (
    relationship_id VARCHAR PRIMARY KEY,
    registry VARCHAR NOT NULL,
    predicate VARCHAR NOT NULL,
    subject_entity_id VARCHAR NOT NULL,
    object_entity_id VARCHAR NOT NULL,
    source_url VARCHAR NOT NULL,
    dataset_date VARCHAR,
    metadata_json VARCHAR NOT NULL DEFAULT '{}',
    updated_at TIMESTAMP DEFAULT current_timestamp
);
CREATE INDEX IF NOT EXISTS registry_relationships_subject_idx
    ON registry_relationships(subject_entity_id);
CREATE INDEX IF NOT EXISTS registry_relationships_object_idx
    ON registry_relationships(object_entity_id);
"""


def ensure_reference_schema(con: duckdb.DuckDBPyConnection) -> None:
    ensure_structured_schema(con)
    ensure_resolution_schema(con)
    con.execute(SCHEMA)


def _record_id(registry: str, record_type: str, external_id: str) -> str:
    return hashlib.sha256(f"{registry}\0{record_type}\0{external_id}".encode()).hexdigest()


def _relationship_id(
    registry: str,
    predicate: str,
    subject_entity_id: str,
    object_entity_id: str,
    external_key: str,
) -> str:
    key = "\0".join(
        [registry, predicate, subject_entity_id, object_entity_id, external_key]
    )
    return hashlib.sha256(key.encode()).hexdigest()


def _put_registry_record(
    con: duckdb.DuckDBPyConnection,
    *,
    registry: str,
    record_type: str,
    external_id: str,
    entity_id: str | None,
    canonical_name: str | None,
    source_url: str,
    dataset_date: str | None,
    metadata: dict | None = None,
) -> None:
    con.execute(
        """INSERT INTO registry_records(
             registry_record_id,registry,record_type,external_id,entity_id,
             canonical_name,source_url,dataset_date,metadata_json
           ) VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(registry_record_id) DO UPDATE SET
             entity_id=excluded.entity_id,
             canonical_name=excluded.canonical_name,
             source_url=excluded.source_url,
             dataset_date=excluded.dataset_date,
             metadata_json=excluded.metadata_json,
             updated_at=now()""",
        [
            _record_id(registry, record_type, external_id),
            registry,
            record_type,
            external_id,
            entity_id,
            canonical_name,
            source_url,
            dataset_date,
            json.dumps(metadata or {}, sort_keys=True, ensure_ascii=False),
        ],
    )


def _put_registry_relationship(
    con: duckdb.DuckDBPyConnection,
    *,
    registry: str,
    predicate: str,
    subject_entity_id: str,
    object_entity_id: str,
    source_url: str,
    dataset_date: str | None,
    external_key: str,
    metadata: dict | None = None,
) -> None:
    relationship_id = _relationship_id(
        registry, predicate, subject_entity_id, object_entity_id, external_key
    )
    con.execute(
        """INSERT INTO registry_relationships(
             relationship_id,registry,predicate,subject_entity_id,object_entity_id,
             source_url,dataset_date,metadata_json
           ) VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(relationship_id) DO UPDATE SET
             source_url=excluded.source_url,
             dataset_date=excluded.dataset_date,
             metadata_json=excluded.metadata_json,
             updated_at=now()""",
        [
            relationship_id,
            registry,
            predicate,
            subject_entity_id,
            object_entity_id,
            source_url,
            dataset_date,
            json.dumps(metadata or {}, sort_keys=True, ensure_ascii=False),
        ],
    )


def _candidate_aliases(con: duckdb.DuckDBPyConnection) -> set[str]:
    refresh_canonical_aliases(con)
    rows = con.execute(
        """SELECT DISTINCT normalized_alias
           FROM entity_aliases
           WHERE length(normalized_alias) >= 5"""
    ).fetchall()
    return {row[0] for row in rows if row[0]}


def _dataset_date(url: str) -> str | None:
    match = re.search(r"(20\d{2}-\d{2}-\d{2})", url)
    return match.group(1) if match else None


def _get_html(url: str) -> str:
    response = httpx.get(
        url,
        timeout=60.0,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    return response.text


def _discover_links(page_url: str, pattern: str) -> list[str]:
    soup = BeautifulSoup(_get_html(page_url), "html.parser")
    regex = re.compile(pattern, re.IGNORECASE)
    links: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = urljoin(page_url, anchor["href"])
        if regex.search(href) and href not in links:
            links.append(href)
    return links


def _discover_charity_link(page_url: str, dataset: str) -> str:
    soup = BeautifulSoup(_get_html(page_url), "html.parser")
    wanted = dataset.casefold()
    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        first = " ".join(cells[0].stripped_strings).strip().casefold()
        if first != wanted:
            continue
        anchors = row.find_all("a", href=True)
        if not anchors:
            continue
        for anchor in reversed(anchors):
            text = " ".join(anchor.stripped_strings).casefold()
            if "text" in text:
                return urljoin(page_url, anchor["href"])
        return urljoin(page_url, anchors[-1]["href"])
    raise RuntimeError(f"Could not discover Charity Commission dataset: {dataset}")


def _download(url: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0:
        return destination
    tmp = destination.with_suffix(destination.suffix + ".part")
    with httpx.stream(
        "GET",
        url,
        timeout=httpx.Timeout(60.0, read=600.0),
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as response:
        response.raise_for_status()
        with tmp.open("wb") as handle:
            for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                handle.write(chunk)
    tmp.replace(destination)
    return destination


def _first_zip_member(archive: zipfile.ZipFile, suffixes: tuple[str, ...]) -> str:
    for name in archive.namelist():
        if name.casefold().endswith(suffixes):
            return name
    raise RuntimeError(f"No supported data file in {archive.filename}")


def _row_value(
    row: dict[str | None, str | list[str] | None], *names: str
) -> str:
    """Return a named CSV value while tolerating malformed/overflow columns.

    ``csv.DictReader`` stores surplus fields under a ``None`` key as a list.
    External bulk datasets occasionally contain such rows, so that synthetic
    overflow entry must not be treated as an ordinary string-valued column.
    """
    normalized: dict[str, str] = {}
    for key, value in row.items():
        if key is None:
            continue
        if isinstance(value, list):
            cell = " ".join(
                str(item).strip() for item in value if item is not None
            ).strip()
        else:
            cell = str(value or "").strip()
        normalized[re.sub(r"[^a-z0-9]+", "", key.casefold())] = cell
    for name in names:
        value = normalized.get(re.sub(r"[^a-z0-9]+", "", name.casefold()))
        if value:
            return value
    return ""


def import_companies_house_basic(
    con: duckdb.DuckDBPyConnection,
    zip_path: Path,
    *,
    source_url: str,
) -> dict[str, int]:
    ensure_reference_schema(con)
    candidates = _candidate_aliases(con)
    stats = {"rows": 0, "matched_companies": 0}
    with zipfile.ZipFile(zip_path) as archive:
        member = _first_zip_member(archive, (".csv",))
        with archive.open(member) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
            for row in reader:
                stats["rows"] += 1
                company_name = _row_value(row, "CompanyName", "Company Name")
                company_number = _row_value(row, "CompanyNumber", "Company Number")
                if not company_name or not company_number:
                    continue
                aliases = [company_name]
                for key, value in row.items():
                    if key is None:
                        continue
                    compact = re.sub(r"[^a-z0-9]+", "", key.casefold())
                    if "previousname" in compact and "companyname" in compact and value:
                        aliases.append(str(value).strip())
                if not any(normalize_org_name(alias) in candidates for alias in aliases if alias):
                    continue
                entity_id = upsert_entity(
                    con,
                    entity_type="COMPANY",
                    name=company_name,
                    namespace="companies_house",
                    metadata={
                        "source_system": "Companies House bulk data",
                        "identifiers": [{"scheme": "GB-COH", "id": company_number}],
                    },
                )
                add_identifier(
                    con,
                    entity_id=entity_id,
                    scheme="GB-COH",
                    identifier=company_number,
                    source="Companies House bulk data",
                )
                for index, alias in enumerate(aliases):
                    add_alias(
                        con,
                        entity_id=entity_id,
                        alias_text=alias,
                        alias_type="registered_name" if index == 0 else "previous_name",
                        source="Companies House bulk data",
                    )
                sic = [
                    str(value).strip()
                    for key, value in row.items()
                    if key
                    and "siccode" in re.sub(r"[^a-z0-9]+", "", key.casefold())
                    and value
                ]
                _put_registry_record(
                    con,
                    registry="companies_house",
                    record_type="company",
                    external_id=company_number,
                    entity_id=entity_id,
                    canonical_name=company_name,
                    source_url=source_url,
                    dataset_date=_dataset_date(source_url),
                    metadata={
                        "status": _row_value(row, "CompanyStatus", "Company Status"),
                        "category": _row_value(row, "CompanyCategory", "Company Category"),
                        "sic": sic,
                    },
                )
                stats["matched_companies"] += 1
    return stats


def import_charity_register(
    con: duckdb.DuckDBPyConnection,
    zip_path: Path,
    *,
    source_url: str,
) -> dict[str, int]:
    ensure_reference_schema(con)
    candidates = _candidate_aliases(con)
    stats = {"rows": 0, "matched_charities": 0}
    with zipfile.ZipFile(zip_path) as archive:
        member = _first_zip_member(archive, (".txt", ".tsv", ".csv"))
        with archive.open(member) as raw:
            sample = raw.read(8192)
            raw.seek(0)
            delimiter = "\t" if b"\t" in sample else ","
            reader = csv.DictReader(
                io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""),
                delimiter=delimiter,
            )
            for row in reader:
                stats["rows"] += 1
                charity_number = _row_value(
                    row, "regno", "registered_charity_number", "charity_number"
                )
                name = _row_value(row, "name", "charity_name")
                if not charity_number or not name:
                    continue
                if normalize_org_name(name) not in candidates:
                    continue
                entity_id = upsert_entity(
                    con,
                    entity_type="CHARITY",
                    name=name,
                    namespace="charity_commission",
                    metadata={
                        "source_system": "Charity Commission bulk register",
                        "identifiers": [{"scheme": "GB-CHC", "id": charity_number}],
                    },
                )
                add_identifier(
                    con,
                    entity_id=entity_id,
                    scheme="GB-CHC",
                    identifier=charity_number,
                    source="Charity Commission bulk register",
                )
                add_alias(
                    con,
                    entity_id=entity_id,
                    alias_text=name,
                    alias_type="registered_name",
                    source="Charity Commission bulk register",
                )
                company_number = _row_value(row, "company_number", "companyno")
                if company_number:
                    add_identifier(
                        con,
                        entity_id=entity_id,
                        scheme="GB-COH",
                        identifier=company_number,
                        source="Charity Commission bulk register",
                    )
                _put_registry_record(
                    con,
                    registry="charity_commission",
                    record_type="charity",
                    external_id=charity_number,
                    entity_id=entity_id,
                    canonical_name=name,
                    source_url=source_url,
                    dataset_date=_dataset_date(source_url),
                    metadata={"company_number": company_number or None},
                )
                stats["matched_charities"] += 1
    return stats


def import_charity_other_names(
    con: duckdb.DuckDBPyConnection,
    zip_path: Path,
    *,
    source_url: str,
) -> dict[str, int]:
    ensure_reference_schema(con)
    matched = {
        str(row[0]): row[1]
        for row in con.execute(
            """SELECT external_id,entity_id FROM registry_records
               WHERE registry='charity_commission' AND record_type='charity'
                 AND entity_id IS NOT NULL"""
        ).fetchall()
    }
    stats = {"rows": 0, "aliases": 0}
    with zipfile.ZipFile(zip_path) as archive:
        member = _first_zip_member(archive, (".txt", ".tsv", ".csv"))
        with archive.open(member) as raw:
            sample = raw.read(8192)
            raw.seek(0)
            delimiter = "\t" if b"\t" in sample else ","
            reader = csv.DictReader(
                io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""),
                delimiter=delimiter,
            )
            for row in reader:
                stats["rows"] += 1
                charity_number = _row_value(
                    row, "regno", "registered_charity_number", "charity_number"
                )
                entity_id = matched.get(charity_number)
                if not entity_id:
                    continue
                other_name = _row_value(row, "other_name", "name", "charity_other_name")
                if not other_name:
                    continue
                add_alias(
                    con,
                    entity_id=entity_id,
                    alias_text=other_name,
                    alias_type="other_name",
                    source="Charity Commission bulk register",
                )
                stats["aliases"] += 1
    return stats


def _matched_company_numbers(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    return {
        str(row[0]).upper(): row[1]
        for row in con.execute(
            """SELECT external_id,entity_id FROM registry_records
               WHERE registry='companies_house' AND record_type='company'
                 AND entity_id IS NOT NULL"""
        ).fetchall()
    }


def import_companies_house_psc(
    con: duckdb.DuckDBPyConnection,
    zip_path: Path,
    *,
    source_url: str,
) -> dict[str, int]:
    ensure_reference_schema(con)
    companies = _matched_company_numbers(con)
    stats = {"rows": 0, "matched_psc": 0}
    if not companies:
        return stats
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.namelist():
            if not member.casefold().endswith((".json", ".txt")):
                continue
            with archive.open(member) as raw:
                for binary_line in raw:
                    line = binary_line.decode("utf-8-sig", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    stats["rows"] += 1
                    company_number = str(
                        record.get("company_number") or record.get("companyNumber") or ""
                    ).upper()
                    company_entity = companies.get(company_number)
                    if not company_entity:
                        continue
                    data = record.get("data") if isinstance(record.get("data"), dict) else record
                    name = str(data.get("name") or "").strip()
                    if not name:
                        continue
                    kind = str(record.get("kind") or data.get("kind") or "").casefold()
                    entity_type = "PERSON" if "individual" in kind else "ORGANISATION"
                    psc_entity = upsert_entity(
                        con,
                        entity_type=entity_type,
                        name=name,
                        namespace="companies_house_psc",
                        metadata={"source_system": "Companies House PSC bulk data"},
                    )
                    if entity_type != "PERSON":
                        identification = data.get("identification")
                        if isinstance(identification, dict):
                            regno = identification.get("registration_number")
                            if regno:
                                add_identifier(
                                    con,
                                    entity_id=psc_entity,
                                    scheme="GB-COH",
                                    identifier=str(regno),
                                    source="Companies House PSC bulk data",
                                )
                    nature = data.get("natures_of_control") or []
                    external_key = str(
                        record.get("links", {}).get("self")
                        if isinstance(record.get("links"), dict)
                        else ""
                    ) or f"{company_number}:{name}:{','.join(map(str, nature))}"
                    _put_registry_relationship(
                        con,
                        registry="companies_house",
                        predicate="PSC_OF_COMPANY",
                        subject_entity_id=psc_entity,
                        object_entity_id=company_entity,
                        source_url=source_url,
                        dataset_date=_dataset_date(source_url),
                        external_key=external_key,
                        metadata={"natures_of_control": nature},
                    )
                    stats["matched_psc"] += 1
    return stats


def refresh_reference_index(
    con: duckdb.DuckDBPyConnection,
    *,
    cache_dir: Path,
    include_psc: bool = False,
) -> dict[str, object]:
    ensure_reference_schema(con)
    cache_dir.mkdir(parents=True, exist_ok=True)

    company_page = "https://download.companieshouse.gov.uk/en_output.html"
    company_links = _discover_links(
        company_page, r"BasicCompanyDataAsOneFile-\d{4}-\d{2}-\d{2}\.zip$"
    )
    if not company_links:
        raise RuntimeError("Companies House basic company snapshot link not found")
    company_url = company_links[-1]
    company_path = _download(company_url, cache_dir / Path(urlsplit(company_url).path).name)
    stats: dict[str, object] = {
        "companies_house": import_companies_house_basic(
            con, company_path, source_url=company_url
        )
    }

    charity_page = (
        "https://register-of-charities.charitycommission.gov.uk/en/register/"
        "full-register-download"
    )
    charity_url = _discover_charity_link(charity_page, "charity")
    charity_names_url = _discover_charity_link(charity_page, "charity_other_names")
    charity_path = _download(
        charity_url, cache_dir / Path(urlsplit(charity_url).path).name
    )
    charity_names_path = _download(
        charity_names_url, cache_dir / Path(urlsplit(charity_names_url).path).name
    )
    stats["charity_commission"] = import_charity_register(
        con, charity_path, source_url=charity_url
    )
    stats["charity_other_names"] = import_charity_other_names(
        con, charity_names_path, source_url=charity_names_url
    )

    if include_psc:
        psc_page = "https://download.companieshouse.gov.uk/en_pscdata.html"
        psc_links = _discover_links(psc_page, r"\.zip$")
        psc_stats = {"files": 0, "rows": 0, "matched_psc": 0}
        for index, psc_url in enumerate(psc_links, start=1):
            with tempfile.TemporaryDirectory(prefix="psc-") as tmp:
                temp_path = Path(tmp) / f"psc-{index}.zip"
                _download(psc_url, temp_path)
                result = import_companies_house_psc(
                    con, temp_path, source_url=psc_url
                )
                psc_stats["files"] += 1
                psc_stats["rows"] += result["rows"]
                psc_stats["matched_psc"] += result["matched_psc"]
        stats["companies_house_psc"] = psc_stats

    stats["resolution"] = run_resolution(con)
    return stats
