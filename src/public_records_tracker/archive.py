from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import Record


@dataclass(frozen=True, slots=True)
class ArchivedSnapshot:
    snapshot_id: str
    observation_id: str
    sha256: str
    canonical_url: str
    archive_path: str
    metadata_path: str
    observation_path: str
    retrieved_at: str


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url)
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, query, ""))


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _extension(content_type: str, url: str) -> str:
    lowered = content_type.lower()
    mapping = {
        "application/pdf": ".pdf",
        "application/json": ".json",
        "text/json": ".json",
        "text/csv": ".csv",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "text/html": ".html",
    }
    for key, ext in mapping.items():
        if key in lowered:
            return ext
    suffix = Path(urlsplit(url).path).suffix.lower()
    if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
        return suffix
    return ".bin"


def archive_record(record: Record, root: Path) -> ArchivedSnapshot:
    now = datetime.now(timezone.utc)
    retrieved_at = now.isoformat()
    canonical_url = canonicalize_url(record.url)
    digest = sha256_bytes(record.body)
    snapshot_id = hashlib.sha256(
        f"{record.source_id}\0{canonical_url}\0{digest}".encode("utf-8")
    ).hexdigest()
    observation_id = hashlib.sha256(
        f"{snapshot_id}\0{retrieved_at}".encode("utf-8")
    ).hexdigest()

    # Store byte-identical content once, independent of retrieval date or URL.
    blob_dir = root / "blobs" / "sha256" / digest[:2] / digest[2:4]
    blob_dir.mkdir(parents=True, exist_ok=True)
    body_path = blob_dir / f"{digest}{_extension(record.content_type, record.url)}"
    if not body_path.exists():
        body_path.write_bytes(record.body)

    # Snapshot metadata identifies this source URL + exact content hash.
    meta_dir = root / "snapshots" / record.source_id / snapshot_id[:2]
    meta_dir.mkdir(parents=True, exist_ok=True)
    meta_path = meta_dir / f"{snapshot_id}.json"
    if not meta_path.exists():
        meta_path.write_text(
            json.dumps(
                {
                    "snapshot_id": snapshot_id,
                    "source_id": record.source_id,
                    "url": record.url,
                    "canonical_url": canonical_url,
                    "title": record.title,
                    "first_retrieved_at": retrieved_at,
                    "sha256": digest,
                    "archive_path": body_path.as_posix(),
                    "content_type": record.content_type,
                    "published_at": record.published_at,
                    "evidence_class": record.evidence_class.value,
                    "metadata": record.metadata,
                },
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    # Every retrieval is preserved separately without duplicating the source bytes.
    obs_dir = root / "observations" / now.strftime("%Y/%m/%d") / record.source_id
    obs_dir.mkdir(parents=True, exist_ok=True)
    observation_path = obs_dir / f"{observation_id}.json"
    observation_path.write_text(
        json.dumps(
            {
                "observation_id": observation_id,
                "snapshot_id": snapshot_id,
                "source_id": record.source_id,
                "canonical_url": canonical_url,
                "retrieved_at": retrieved_at,
                "status_code": record.status_code,
                "etag": record.etag,
                "last_modified": record.last_modified,
            },
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    return ArchivedSnapshot(
        snapshot_id=snapshot_id,
        observation_id=observation_id,
        sha256=digest,
        canonical_url=canonical_url,
        archive_path=body_path.as_posix(),
        metadata_path=meta_path.as_posix(),
        observation_path=observation_path.as_posix(),
        retrieved_at=retrieved_at,
    )
