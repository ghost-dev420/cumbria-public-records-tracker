from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

from .db import connect


CATEGORY_LABELS = {
    "contract_coverage_gap": "Contract transparency gaps",
    "documented_contract_link": "Documented payment / contract links",
    "reviewed_value_concern": "Reviewed value-for-money concerns",
    "reviewed_connection": "Reviewed declared-interest connections",
    "documented_finding": "Documented findings",
    "other": "Other reviewed findings",
}


def _json(value: str | None, default):
    try:
        parsed = json.loads(value or "")
    except (json.JSONDecodeError, TypeError):
        return default
    return parsed


def _money(metadata: dict) -> float | None:
    for key in (
        "payment_total_gbp",
        "net_payment_total_gbp",
        "gross_positive_payments_gbp",
        "tracked_contract_value_gbp",
    ):
        value = metadata.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _category(signal_type: str) -> str:
    if signal_type in {
        "PAYMENT_STREAM_WITHOUT_TRACKED_PROCUREMENT_LINK",
        "PAYMENTS_OUTSIDE_TRACKED_CONTRACT_PERIODS",
    }:
        return "contract_coverage_gap"
    if signal_type == "SUPPLIER_PAYMENT_CONTRACT_OVERLAP":
        return "documented_contract_link"
    if signal_type == "PAYMENTS_EXCEED_TRACKED_CONTRACT_VALUE":
        return "reviewed_value_concern"
    if signal_type == "DECLARED_INTEREST_SUPPLIER_OVERLAP":
        return "reviewed_connection"
    return "other"


def _why_flagged(signal_type: str) -> str:
    return {
        "PAYMENT_STREAM_WITHOUT_TRACKED_PROCUREMENT_LINK": (
            "Published payments exceed the tracker's discovery threshold, but no "
            "buyer-scoped supplier-to-contract relationship is currently indexed."
        ),
        "PAYMENTS_OUTSIDE_TRACKED_CONTRACT_PERIODS": (
            "One or more positive published payments fall outside every complete "
            "buyer-scoped contract period currently indexed for the supplier."
        ),
        "SUPPLIER_PAYMENT_CONTRACT_OVERLAP": (
            "Independent public payment and procurement records resolve to the same supplier."
        ),
        "PAYMENTS_EXCEED_TRACKED_CONTRACT_VALUE": (
            "Reviewed payment totals are higher than the tracked buyer-scoped contract values."
        ),
        "DECLARED_INTEREST_SUPPLIER_OVERLAP": (
            "A reviewed declared-interest record refers to an entity also present in supplier records."
        ),
    }.get(signal_type, "This item passed the publication review boundary.")


def _resolution_needed(signal_type: str) -> str:
    return {
        "PAYMENT_STREAM_WITHOUT_TRACKED_PROCUREMENT_LINK": (
            "Locate the applicable contract, framework, call-off, purchase order, direct award, "
            "variation or other procurement record covering the payment stream."
        ),
        "PAYMENTS_OUTSIDE_TRACKED_CONTRACT_PERIODS": (
            "Locate an extension, variation, replacement contract or other record covering the "
            "payment dates outside the currently indexed periods."
        ),
        "SUPPLIER_PAYMENT_CONTRACT_OVERLAP": "No action required; this is a documented cross-source link.",
        "PAYMENTS_EXCEED_TRACKED_CONTRACT_VALUE": (
            "Confirm that contract coverage is complete and reconcile variations, VAT, credits, "
            "framework call-offs and any other legitimate payment streams."
        ),
        "DECLARED_INTEREST_SUPPLIER_OVERLAP": (
            "Read the underlying declaration and supplier evidence to establish the exact nature "
            "of the relationship. No family, friendship or conflict is inferred automatically."
        ),
    }.get(signal_type, "Review the linked evidence and source records.")


def _fact_evidence(con, fact_ids: list[str]) -> list[dict]:
    if not fact_ids:
        return []
    placeholders = ",".join("?" for _ in fact_ids)
    rows = con.execute(
        f"""SELECT f.fact_id,f.predicate,f.value_text,f.locator,f.evidence_class,
                   d.canonical_url,s.sha256
            FROM facts f
            JOIN documents d ON d.document_id=f.document_id
            JOIN snapshots s ON s.snapshot_id=f.snapshot_id
            WHERE f.fact_id IN ({placeholders})
            ORDER BY f.fact_type,f.fact_id""",
        fact_ids,
    ).fetchall()
    return [
        {
            "fact_id": str(fact_id),
            "predicate": str(predicate or ""),
            "value_text": value_text,
            "locator": locator,
            "evidence_class": evidence_class,
            "source_url": source_url,
            "sha256": sha256,
        }
        for fact_id, predicate, value_text, locator, evidence_class, source_url, sha256 in rows
    ]


def _approved_claims(con) -> list[dict]:
    rows = con.execute(
        """SELECT claim_id,claim_text,classification
           FROM claims WHERE status='approved'
           ORDER BY created_at DESC,claim_id"""
    ).fetchall()
    return [
        {
            "finding_id": f"claim:{claim_id}",
            "signal_type": "APPROVED_CLAIM",
            "category": "documented_finding",
            "category_label": CATEGORY_LABELS["documented_finding"],
            "status_label": "Documented finding",
            "headline": str(claim_text),
            "subject": None,
            "object": None,
            "amount_gbp": None,
            "why_flagged": "This claim passed the tracker's explicit publication review boundary.",
            "what_records_show": str(claim_text),
            "limitations": [
                "Read the linked source material and methodology before drawing broader conclusions."
            ],
            "resolution_needed": "No automated inference is added beyond the approved claim text.",
            "classification": str(classification),
            "evidence": [],
        }
        for claim_id, claim_text, classification in rows
    ]


def build_public_findings(db_path: Path, out_dir: Path) -> dict[str, int]:
    """Build a plain-language accountability dashboard from the sanitized public DB."""
    con = connect(db_path)
    try:
        rows = con.execute(
            """SELECT s.signal_id,s.signal_type,s.summary,s.subject_entity_id,s.object_entity_id,
                      s.evidence_json,s.metadata_json,
                      se.canonical_name AS subject_name,oe.canonical_name AS object_name
               FROM signals s
               LEFT JOIN entities se ON se.entity_id=s.subject_entity_id
               LEFT JOIN entities oe ON oe.entity_id=s.object_entity_id
               WHERE s.status='approved'
               ORDER BY s.score DESC,s.updated_at DESC"""
        ).fetchall()
        findings: list[dict] = []
        for (
            signal_id,
            signal_type,
            summary,
            _subject_id,
            _object_id,
            evidence_json,
            metadata_json,
            subject_name,
            object_name,
        ) in rows:
            metadata = _json(metadata_json, {})
            evidence_ids = _json(evidence_json, [])
            if not isinstance(metadata, dict):
                metadata = {}
            if not isinstance(evidence_ids, list):
                evidence_ids = []
            category = _category(str(signal_type))
            caveats = metadata.get("caveats") or []
            if not isinstance(caveats, list):
                caveats = [str(caveats)]
            status_label = {
                "contract_coverage_gap": "Coverage gap",
                "documented_contract_link": "Documented link",
                "reviewed_value_concern": "Reviewed concern",
                "reviewed_connection": "Reviewed connection",
            }.get(category, "Reviewed finding")
            findings.append(
                {
                    "finding_id": str(signal_id),
                    "signal_type": str(signal_type),
                    "category": category,
                    "category_label": CATEGORY_LABELS[category],
                    "status_label": status_label,
                    "headline": str(summary),
                    "subject": subject_name,
                    "object": object_name,
                    "amount_gbp": _money(metadata),
                    "why_flagged": _why_flagged(str(signal_type)),
                    "what_records_show": str(summary),
                    "limitations": [str(item) for item in caveats],
                    "resolution_needed": _resolution_needed(str(signal_type)),
                    "metadata": metadata,
                    "evidence": _fact_evidence(con, [str(item) for item in evidence_ids]),
                }
            )
        findings.extend(_approved_claims(con))
    finally:
        con.close()

    counts = {key: 0 for key in CATEGORY_LABELS}
    for item in findings:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    total_amount = sum(float(item["amount_gbp"] or 0.0) for item in findings)
    generated_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "notice": (
            "Evidence-first public records dashboard. Coverage gaps and analytical observations "
            "are not findings of wrongdoing. Relationship labels are published only after the "
            "publication boundary is satisfied."
        ),
        "generated_at": generated_at,
        "counts": counts,
        "amount_represented_gbp": total_amount,
        "findings": findings,
    }

    api_dir = out_dir / "api"
    api_dir.mkdir(parents=True, exist_ok=True)
    (api_dir / "findings.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )

    css = """
    :root{font-family:system-ui,sans-serif;color:#171717;background:#f5f6f8}
    body{margin:0}main{max-width:1250px;margin:auto;padding:2rem}.muted{color:#666}
    .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:1rem;margin:1.5rem 0}
    .card,.finding{background:#fff;border:1px solid #ddd;border-radius:12px;padding:1rem}
    .card .n{font-size:2rem;font-weight:700}.finding{margin:1rem 0}.tag{display:inline-block;background:#eef1f4;border-radius:5px;padding:.2rem .4rem;font-size:.8rem}
    .money{font-size:1.35rem;font-weight:700;margin:.5rem 0}.evidence{font-size:.88rem;margin:.35rem 0}
    a{color:#0759a8;overflow-wrap:anywhere}details{margin-top:.8rem}h1{margin-bottom:.25rem}
    """
    cards = "".join(
        f"<div class='card'><div class='n'>{counts.get(key, 0)}</div><div>{html.escape(label)}</div></div>"
        for key, label in CATEGORY_LABELS.items()
        if key != "other"
    )
    items_html: list[str] = []
    for item in sorted(
        findings,
        key=lambda row: (float(row.get("amount_gbp") or 0.0), row.get("headline") or ""),
        reverse=True,
    ):
        amount = item.get("amount_gbp")
        money = f"<div class='money'>£{float(amount):,.2f}</div>" if amount is not None else ""
        limitations = "".join(f"<li>{html.escape(str(value))}</li>" for value in item.get("limitations") or [])
        evidence = "".join(
            "<div class='evidence'>"
            f"<strong>{html.escape(str(row.get('predicate') or 'evidence'))}</strong> — "
            f"{html.escape(str(row.get('locator') or 'source record'))} · "
            f"<a rel='noreferrer' href='{html.escape(str(row.get('source_url') or ''), quote=True)}'>official source</a>"
            "</div>"
            for row in item.get("evidence") or []
        ) or "<p class='muted'>No fact-level evidence chain attached to this item.</p>"
        items_html.append(
            "<article class='finding'>"
            f"<span class='tag'>{html.escape(item['status_label'])}</span> "
            f"<span class='tag'>{html.escape(item['category_label'])}</span>"
            f"<h2>{html.escape(item['headline'])}</h2>{money}"
            f"<p><strong>Why flagged:</strong> {html.escape(item['why_flagged'])}</p>"
            f"<p><strong>What would resolve it:</strong> {html.escape(item['resolution_needed'])}</p>"
            "<details><summary>Limitations and evidence</summary>"
            f"<ul>{limitations}</ul>{evidence}</details></article>"
        )
    page = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>Accountability findings</title><style>{css}</style></head><body><main>"
        "<p><a href='index.html'>← Main tracker</a></p><h1>Public accountability dashboard</h1>"
        "<p class='muted'>Coverage gaps are not allegations. The site distinguishes documented records, "
        "coverage gaps, reviewed connections and reviewed findings so that absence of a record is never "
        "presented as proof of misconduct.</p>"
        f"<div class='cards'>{cards}</div>"
        f"<p class='muted'>Generated {html.escape(generated_at)}. Amounts are not additive measures of waste.</p>"
        + "".join(items_html)
        + "</main></body></html>"
    )
    (out_dir / "findings.html").write_text(page, encoding="utf-8")

    index_path = out_dir / "index.html"
    if index_path.exists():
        page = index_path.read_text(encoding="utf-8")
        if "href='findings.html'" not in page:
            page = page.replace(
                "<div class='nav'>",
                "<div class='nav'><a class='button' href='findings.html'>Accountability dashboard</a>",
                1,
            )
        headline = (
            "<section><h2>Accountability overview</h2><div class='cards'>"
            f"<div class='card'><div class='n'>{counts.get('contract_coverage_gap', 0)}</div><div>contract transparency gaps</div></div>"
            f"<div class='card'><div class='n'>{counts.get('reviewed_connection', 0)}</div><div>reviewed declared-interest connections</div></div>"
            f"<div class='card'><div class='n'>{counts.get('reviewed_value_concern', 0)}</div><div>reviewed value concerns</div></div>"
            f"<div class='card'><div class='n'>{counts.get('documented_finding', 0)}</div><div>documented findings</div></div>"
            "</div><p class='muted'>A coverage gap means the tracker has not yet located a matching record; it is not evidence of wrongdoing. "
            "Amounts shown in findings are not automatically waste.</p></section>"
        )
        if "<h2>Accountability overview</h2>" not in page:
            page = page.replace("</div>", "</div>" + headline, 1)
        index_path.write_text(page, encoding="utf-8")

    api_index = api_dir / "index.json"
    if api_index.exists():
        index = json.loads(api_index.read_text(encoding="utf-8"))
        endpoints = index.get("endpoints") or {}
        endpoints["findings"] = "api/findings.json"
        index["endpoints"] = endpoints
        api_index.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        "public_findings": len(findings),
        "contract_coverage_gaps": counts.get("contract_coverage_gap", 0),
        "reviewed_connections": counts.get("reviewed_connection", 0),
        "reviewed_value_concerns": counts.get("reviewed_value_concern", 0),
        "documented_findings": counts.get("documented_finding", 0),
    }
