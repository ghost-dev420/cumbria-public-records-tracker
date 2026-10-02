from __future__ import annotations

import hashlib
import html
import json
import zipfile
from pathlib import Path

from .db import connect


def _rows(con, query: str, params: list | None = None) -> list[dict]:
    cursor = con.execute(query, params or [])
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _fact_evidence(con, fact_ids: list[str]) -> list[dict]:
    if not fact_ids:
        return []
    placeholders = ",".join("?" for _ in fact_ids)
    return _rows(
        con,
        f"""SELECT f.fact_id,f.fact_type,f.predicate,
                   se.canonical_name AS subject_name,
                   oe.canonical_name AS object_name,
                   f.value_text,f.locator,f.evidence_class,f.metadata_json,
                   d.document_id,d.title,d.canonical_url AS source_url,
                   s.snapshot_id,s.sha256,s.retrieved_at,s.content_type,
                   s.archive_path
            FROM facts f
            JOIN documents d ON d.document_id=f.document_id
            JOIN snapshots s ON s.snapshot_id=f.snapshot_id
            LEFT JOIN entities se ON se.entity_id=f.subject_entity_id
            LEFT JOIN entities oe ON oe.entity_id=f.object_entity_id
            WHERE f.fact_id IN ({placeholders})
            ORDER BY d.canonical_url,f.locator,f.fact_id""",
        fact_ids,
    )


def _verification_text(manifest: dict) -> str:
    lines = [
        "Cumbria Public Records Evidence Tracker - verification guide",
        "",
        "This package is an evidence manifest, not a finding of wrongdoing.",
        "Raw source bytes are not redistributed by default. Use the official source URL",
        "and the recorded locator and SHA-256 to verify the underlying record.",
        "",
        f"Signal: {manifest['signal']['signal_type']}",
        f"Summary: {manifest['signal']['summary']}",
        "",
        "Evidence:",
    ]
    for index, item in enumerate(manifest["evidence"], start=1):
        lines.extend(
            [
                f"{index}. {item.get('predicate') or item.get('fact_type')}",
                f"   Source: {item.get('source_url') or ''}",
                f"   Locator: {item.get('locator') or ''}",
                f"   Snapshot SHA-256: {item.get('sha256') or ''}",
                f"   Retrieved: {item.get('retrieved_at') or ''}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _inject_nav(out_dir: Path) -> None:
    index_path = out_dir / "index.html"
    if not index_path.exists():
        return
    page = index_path.read_text(encoding="utf-8")
    if "evidence-packages.html" in page:
        return
    marker = "<a class='button' href='api/index.json'>API</a>"
    link = "<a class='button' href='evidence-packages.html'>Evidence packages</a>"
    if marker in page:
        page = page.replace(marker, marker + link, 1)
        index_path.write_text(page, encoding="utf-8")


def build_evidence_packages(db_path: Path, out_dir: Path) -> int:
    con = connect(db_path)
    signals = _rows(
        con,
        """SELECT s.signal_id,s.signal_type,s.status,s.summary,
                  s.subject_entity_id,s.object_entity_id,s.evidence_json,
                  s.metadata_json,s.created_at,s.updated_at,
                  se.canonical_name AS subject_name,
                  oe.canonical_name AS object_name
           FROM signals s
           LEFT JOIN entities se ON se.entity_id=s.subject_entity_id
           LEFT JOIN entities oe ON oe.entity_id=s.object_entity_id
           WHERE s.status='approved'
           ORDER BY s.updated_at DESC,s.signal_id""",
    )

    package_dir = out_dir / "evidence-packages"
    package_dir.mkdir(parents=True, exist_ok=True)
    listing: list[dict] = []

    for signal in signals:
        try:
            fact_ids = json.loads(signal.get("evidence_json") or "[]")
        except json.JSONDecodeError:
            fact_ids = []
        if not isinstance(fact_ids, list):
            fact_ids = []
        evidence = _fact_evidence(con, [str(item) for item in fact_ids])
        manifest = {
            "notice": (
                "Evidence manifest for verification. A documented relationship or review signal "
                "is not a finding of wrongdoing. Raw source bytes are not redistributed by default."
            ),
            "signal": {
                key: signal.get(key)
                for key in (
                    "signal_id",
                    "signal_type",
                    "status",
                    "summary",
                    "subject_entity_id",
                    "subject_name",
                    "object_entity_id",
                    "object_name",
                    "metadata_json",
                    "created_at",
                    "updated_at",
                )
            },
            "evidence": evidence,
        }
        manifest_bytes = (
            json.dumps(manifest, default=str, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        guide = _verification_text(manifest).encode("utf-8")
        zip_path = package_dir / f"{signal['signal_id']}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", manifest_bytes)
            archive.writestr("VERIFY.txt", guide)
            archive.writestr("manifest.sha256", f"{manifest_sha}  manifest.json\n")
        listing.append(
            {
                "signal_id": signal["signal_id"],
                "signal_type": signal["signal_type"],
                "summary": signal["summary"],
                "subject_name": signal.get("subject_name"),
                "object_name": signal.get("object_name"),
                "evidence_count": len(evidence),
                "manifest_sha256": manifest_sha,
                "download": f"evidence-packages/{signal['signal_id']}.zip",
            }
        )
    con.close()

    api_dir = out_dir / "api"
    api_dir.mkdir(parents=True, exist_ok=True)
    (api_dir / "evidence-packages.json").write_text(
        json.dumps(listing, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    table_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(str(item['signal_type']))}</code></td>"
        f"<td>{html.escape(str(item['summary']))}</td>"
        f"<td>{html.escape(str(item['subject_name'] or ''))}</td>"
        f"<td>{html.escape(str(item['object_name'] or ''))}</td>"
        f"<td>{int(item['evidence_count'])}</td>"
        f"<td><a href='{html.escape(str(item['download']), quote=True)}'>download ZIP</a></td>"
        "</tr>"
        for item in listing
    ) or "<tr><td colspan='6'>No approved evidence packages are available yet.</td></tr>"

    page = f"""<!doctype html>
<html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Evidence packages — Cumbria Public Records Evidence Tracker</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:1250px;margin:auto;padding:2rem;color:#171717}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:.6rem;border-bottom:1px solid #ddd;text-align:left;vertical-align:top}}
code{{background:#eef1f4;padding:.1rem .25rem;border-radius:4px}}.note{{background:#f6f7f8;border:1px solid #ddd;border-radius:10px;padding:1rem}}
</style></head><body>
<p><a href='index.html'>← Main tracker</a></p>
<h1>Downloadable evidence packages</h1>
<p class='note'>Each ZIP contains a machine-readable manifest, verification instructions and a checksum for the manifest. It records the exact official source URL, locator, retrieval time and snapshot SHA-256 for each supporting fact. Raw source files are not redistributed by default.</p>
<table><thead><tr><th>Type</th><th>Summary</th><th>Subject</th><th>Entity</th><th>Evidence records</th><th>Package</th></tr></thead><tbody>{table_rows}</tbody></table>
</body></html>"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "evidence-packages.html").write_text(page, encoding="utf-8")
    _inject_nav(out_dir)
    return len(listing)
