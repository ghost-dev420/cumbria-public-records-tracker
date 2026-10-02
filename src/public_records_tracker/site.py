from __future__ import annotations

import html
import json
from pathlib import Path

import yaml

from .analysis import ensure_analysis_schema
from .db import connect, dashboard_data
from .health import ensure_health_schema
from .reference import ensure_reference_schema
from .resolution import ensure_resolution_schema
from .structured import ensure_structured_schema


CSS = """
:root{font-family:system-ui,sans-serif;color:#171717;background:#f6f7f8}
body{margin:0}main{max-width:1250px;margin:auto;padding:2rem}
h1{margin-bottom:.25rem}h2{margin-top:.25rem}.muted{color:#666}.warn{color:#7a3e00}
.nav{display:flex;gap:.65rem;flex-wrap:wrap;margin:1.25rem 0 1.75rem}
.button{display:inline-block;text-decoration:none;border:1px solid #bbb;border-radius:8px;padding:.55rem .8rem;background:white;color:#171717}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1rem;margin:2rem 0}
.card,section{background:white;border:1px solid #ddd;border-radius:12px;padding:1rem;margin-bottom:1rem}
.n{font-size:2rem;font-weight:700}
table{width:100%;border-collapse:collapse;font-size:.9rem}
th,td{text-align:left;padding:.55rem;border-bottom:1px solid #eee;vertical-align:top}
a{color:#0759a8;overflow-wrap:anywhere}
.tag{font-family:monospace;font-size:.8rem;background:#eef1f4;padding:.15rem .35rem;border-radius:5px}
.status-success{color:#166534}.status-blocked,.status-error,.status-network_error{color:#991b1b}
code{background:#eef1f4;padding:.1rem .25rem;border-radius:4px}
.example{border-left:4px solid #bbb;padding-left:1rem;margin:1rem 0}
.evidence{font-size:.88rem;margin:.45rem 0}.sha{font-family:monospace;font-size:.78rem}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;border:1px solid #ddd;border-radius:12px;padding:1rem}
"""


def _rows(con, query: str, params: list | None = None) -> list[dict]:
    cursor = con.execute(query, params or [])
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _json(value: str | None, default):
    try:
        return json.loads(value or "")
    except (json.JSONDecodeError, TypeError):
        return default


def _load_public_config() -> dict:
    path = Path("config/public.yml")
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _fact_evidence(con, fact_ids: list[str]) -> list[dict]:
    if not fact_ids:
        return []
    placeholders = ",".join("?" for _ in fact_ids)
    return _rows(
        con,
        f"""SELECT f.fact_id,f.fact_type,f.predicate,
                   se.canonical_name AS subject_name,
                   oe.canonical_name AS object_name,
                   f.value_text,f.locator,f.evidence_class,
                   d.canonical_url AS source_url,s.sha256,s.archive_path,
                   f.snapshot_id
            FROM facts f
            JOIN documents d ON d.document_id=f.document_id
            JOIN snapshots s ON s.snapshot_id=f.snapshot_id
            LEFT JOIN entities se ON se.entity_id=f.subject_entity_id
            LEFT JOIN entities oe ON oe.entity_id=f.object_entity_id
            WHERE f.fact_id IN ({placeholders})
            ORDER BY f.fact_type,f.fact_id""",
        fact_ids,
    )


def _api_payloads(con) -> dict[str, object]:
    entities = _rows(
        con,
        """SELECT e.entity_id,e.entity_type,e.canonical_name,e.normalized_name,
                  coalesce(r.canonical_entity_id,e.entity_id) AS resolved_entity_id,
                  e.metadata_json
           FROM entities e
           LEFT JOIN resolved_entity_members r ON r.entity_id=e.entity_id
           ORDER BY e.entity_type,e.canonical_name""",
    )
    facts = _rows(
        con,
        """SELECT f.fact_id,f.fact_type,f.predicate,f.subject_entity_id,
                  se.canonical_name AS subject_name,
                  f.object_entity_id,oe.canonical_name AS object_name,
                  f.value_text,f.locator,f.evidence_class,f.metadata_json,
                  f.document_id,f.snapshot_id,s.sha256,
                  d.canonical_url AS source_url,s.archive_path,
                  w.valid_from,w.valid_to
           FROM latest_facts f
           JOIN documents d ON d.document_id=f.document_id
           JOIN snapshots s ON s.snapshot_id=f.snapshot_id
           LEFT JOIN fact_snapshot_windows w
             ON w.document_id=f.document_id AND w.snapshot_id=f.snapshot_id
           LEFT JOIN entities se ON se.entity_id=f.subject_entity_id
           LEFT JOIN entities oe ON oe.entity_id=f.object_entity_id
           ORDER BY d.last_seen DESC,f.fact_type,f.fact_id""",
    )
    matches = _rows(
        con,
        """SELECT m.match_id,m.left_entity_id,m.right_entity_id,m.confidence,
                  m.match_method,m.status,m.evidence_json,m.created_at,m.updated_at,
                  le.canonical_name AS left_name,re.canonical_name AS right_name
           FROM entity_matches m
           LEFT JOIN entities le ON le.entity_id=m.left_entity_id
           LEFT JOIN entities re ON re.entity_id=m.right_entity_id
           ORDER BY m.confidence DESC,m.updated_at DESC""",
    )
    reviews = _rows(
        con,
        """SELECT q.review_id,q.item_type,q.status,q.summary,
                  q.subject_entity_id,q.object_entity_id,q.evidence_json,
                  q.metadata_json,q.created_at,q.updated_at,
                  se.canonical_name AS subject_name,oe.canonical_name AS object_name
           FROM review_queue q
           LEFT JOIN entities se ON se.entity_id=q.subject_entity_id
           LEFT JOIN entities oe ON oe.entity_id=q.object_entity_id
           ORDER BY q.status,q.updated_at DESC""",
    )
    signals = _rows(
        con,
        """SELECT s.signal_id,s.signal_type,s.status,s.summary,
                  s.subject_entity_id,s.object_entity_id,s.evidence_json,
                  s.metadata_json,s.created_at,s.updated_at,
                  se.canonical_name AS subject_name,oe.canonical_name AS object_name
           FROM signals s
           LEFT JOIN entities se ON se.entity_id=s.subject_entity_id
           LEFT JOIN entities oe ON oe.entity_id=s.object_entity_id
           ORDER BY s.updated_at DESC,s.signal_id""",
    )
    for item in signals:
        fact_ids = _json(item.get("evidence_json"), [])
        item["evidence"] = _fact_evidence(con, fact_ids if isinstance(fact_ids, list) else [])
    for item in reviews:
        fact_ids = _json(item.get("evidence_json"), [])
        item["evidence"] = _fact_evidence(con, fact_ids if isinstance(fact_ids, list) else [])

    health = _rows(
        con,
        """SELECT source_id,status,record_count,fact_count,error_class,error_message,
                  http_status,started_at,finished_at
           FROM latest_source_health
           ORDER BY status<>'success' DESC,source_id""",
    )
    registry_records = _rows(
        con,
        """SELECT registry,record_type,external_id,entity_id,canonical_name,
                  source_url,dataset_date,metadata_json,updated_at
           FROM registry_records
           ORDER BY registry,record_type,canonical_name""",
    )
    registry_relationships = _rows(
        con,
        """SELECT r.registry,r.predicate,r.subject_entity_id,r.object_entity_id,
                  se.canonical_name AS subject_name,oe.canonical_name AS object_name,
                  r.source_url,r.dataset_date,r.metadata_json,r.updated_at
           FROM registry_relationships r
           LEFT JOIN entities se ON se.entity_id=r.subject_entity_id
           LEFT JOIN entities oe ON oe.entity_id=r.object_entity_id
           ORDER BY r.registry,r.predicate,subject_name,object_name""",
    )
    return {
        "entities": entities,
        "facts": facts,
        "matches": matches,
        "review-queue": reviews,
        "signals": signals,
        "source-health": health,
        "registry-records": registry_records,
        "registry-relationships": registry_relationships,
        "examples": signals[:3],
    }


def _write_api(out_dir: Path, payloads: dict[str, object]) -> None:
    api_dir = out_dir / "api"
    api_dir.mkdir(parents=True, exist_ok=True)
    index = {
        "notice": (
            "Evidence-first public records data. Signals and review items are prompts "
            "for human review, not findings of wrongdoing."
        ),
        "endpoints": {},
    }
    for name, payload in payloads.items():
        filename = f"{name}.json"
        (api_dir / filename).write_text(
            json.dumps(payload, default=str, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        index["endpoints"][name] = f"api/{filename}"
    (api_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _evidence_html(items: list[dict]) -> str:
    if not items:
        return "<p class='muted'>No fact chain attached.</p>"
    rows: list[str] = []
    for item in items:
        source = html.escape(str(item.get("source_url") or ""), quote=True)
        locator = html.escape(str(item.get("locator") or "source record"))
        sha = html.escape(str(item.get("sha256") or ""))
        predicate = html.escape(str(item.get("predicate") or ""))
        rows.append(
            "<div class='evidence'>"
            f"<strong>{predicate}</strong> — {locator} · "
            f"<a rel='noreferrer' href='{source}'>official source</a><br>"
            f"<span class='sha'>SHA-256 {sha}</span>"
            "</div>"
        )
    return "".join(rows)


def _write_methodology(out_dir: Path, config: dict) -> None:
    source = Path("docs/methodology.md")
    text = source.read_text(encoding="utf-8") if source.exists() else "Methodology not available."
    title = html.escape(str(config.get("project_name") or "Public Records Evidence Tracker"))
    page = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>Methodology — {title}</title><style>{CSS}</style></head><body><main>"
        f"<p><a href='index.html'>← Back to {title}</a></p><h1>Methodology</h1>"
        f"<pre>{html.escape(text)}</pre></main></body></html>"
    )
    (out_dir / "methodology.html").write_text(page, encoding="utf-8")


def build_site(db_path: Path, out_dir: Path) -> None:
    con = connect(db_path)
    ensure_structured_schema(con)
    ensure_resolution_schema(con)
    ensure_analysis_schema(con)
    ensure_health_schema(con)
    ensure_reference_schema(con)
    data = dashboard_data(con)
    payloads = _api_payloads(con)
    con.close()

    data["counts"].update(
        {
            "facts": len(payloads["facts"]),
            "entity_matches": len(payloads["matches"]),
            "open_reviews": sum(
                1 for row in payloads["review-queue"] if row["status"] == "open"
            ),
            "signals": len(payloads["signals"]),
            "registry_records": len(payloads["registry-records"]),
        }
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_api(out_dir, payloads)
    config = _load_public_config()
    _write_methodology(out_dir, config)

    cards = "".join(
        f'<div class="card"><div class="n">{int(value)}</div><div>{html.escape(key)}</div></div>'
        for key, value in data["counts"].items()
    )
    docs = "".join(
        "<tr>"
        f"<td>{html.escape(str(row[0]))}</td>"
        f"<td><span class='tag'>{html.escape(str(row[1]))}</span></td>"
        f"<td>{html.escape(str(row[3]))}</td><td>{html.escape(str(row[4] or ''))}</td>"
        f"<td><a rel='noreferrer' href='{html.escape(str(row[2]), quote=True)}'>source</a></td>"
        "</tr>"
        for row in data["recent_docs"]
    )
    changes = "".join(
        "<tr>"
        f"<td><span class='tag'>{html.escape(str(row[0]))}</span></td>"
        f"<td>{html.escape(str(row[2]))}</td>"
        f"<td><a rel='noreferrer' href='{html.escape(str(row[1]), quote=True)}'>source</a></td>"
        f"<td>{html.escape(str(row[5]))}</td></tr>"
        for row in data["recent_changes"]
    ) or "<tr><td colspan='4'>No changed source content recorded yet.</td></tr>"

    health_rows = "".join(
        "<tr>"
        f"<td><span class='tag'>{html.escape(str(row['source_id']))}</span></td>"
        f"<td class='status-{html.escape(str(row['status']))}'>{html.escape(str(row['status']))}</td>"
        f"<td>{int(row['record_count'])}</td><td>{int(row['fact_count'])}</td>"
        f"<td>{html.escape(str(row['http_status'] or ''))}</td>"
        f"<td>{html.escape(str(row['error_message'] or ''))}</td></tr>"
        for row in payloads["source-health"]
    ) or "<tr><td colspan='6'>No source runs recorded yet.</td></tr>"

    signal_rows = "".join(
        "<tr>"
        f"<td><span class='tag'>{html.escape(str(row['signal_type']))}</span></td>"
        f"<td>{html.escape(str(row['summary']))}</td>"
        f"<td>{html.escape(str(row['subject_name'] or ''))}</td>"
        f"<td>{html.escape(str(row['object_name'] or ''))}</td>"
        f"<td>{len(row.get('evidence') or [])}</td></tr>"
        for row in payloads["signals"][:100]
    ) or "<tr><td colspan='5'>No signals yet.</td></tr>"

    review_rows = "".join(
        "<tr>"
        f"<td><span class='tag'>{html.escape(str(row['item_type']))}</span></td>"
        f"<td>{html.escape(str(row['summary']))}</td>"
        f"<td>{html.escape(str(row['status']))}</td>"
        f"<td>{len(row.get('evidence') or [])}</td></tr>"
        for row in payloads["review-queue"][:100]
    ) or "<tr><td colspan='4'>No review items yet.</td></tr>"

    example_rows = "".join(
        "<div class='example'>"
        f"<span class='tag'>{html.escape(str(row['signal_type']))}</span>"
        f"<h3>{html.escape(str(row['summary']))}</h3>"
        "<p class='muted'>Public-record linkage for review; no inference of wrongdoing.</p>"
        f"{_evidence_html(row.get('evidence') or [])}</div>"
        for row in payloads["examples"]
    ) or "<p class='muted'>No evidence-chain examples are available yet.</p>"

    project_name = html.escape(
        str(config.get("project_name") or "Cumbria Public Records Evidence Tracker")
    )
    tagline = html.escape(
        str(
            config.get("tagline")
            or "Structured public records, preserved provenance, and review signals."
        )
    )
    nav = [
        "<a class='button' href='methodology.html'>Methodology</a>",
        "<a class='button' href='api/index.json'>API</a>",
    ]
    suggestion_url = str(config.get("suggestion_url") or "").strip()
    support_url = str(config.get("support_url") or "").strip()
    if suggestion_url:
        nav.append(
            f"<a class='button' rel='noreferrer' href='{html.escape(suggestion_url, quote=True)}'>"
            "Suggest a public record</a>"
        )
    if support_url:
        nav.append(
            f"<a class='button' rel='noreferrer' href='{html.escape(support_url, quote=True)}'>"
            "Support the project</a>"
        )

    page = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{project_name}</title><style>{CSS}</style></head><body><main>
<h1>{project_name}</h1>
<p>{tagline}</p>
<p class='muted'>This project indexes and links public records. A relationship, review item or signal is not a finding of wrongdoing. Follow the underlying records and provenance before drawing conclusions.</p>
<div class='nav'>{''.join(nav)}</div>
<div class='cards'>{cards}</div>
<section><h2>Evidence-chain examples</h2>
<p class='muted'>A small set of recently generated review signals, shown with their source chain rather than a severity score.</p>
{example_rows}</section>
<section><h2>Source health</h2><p class='muted'>Blocked or broken public sources are recorded rather than silently ignored.</p>
<table><thead><tr><th>Source</th><th>Status</th><th>Records</th><th>Facts</th><th>HTTP</th><th>Detail</th></tr></thead><tbody>{health_rows}</tbody></table></section>
<section><h2>Signals for review</h2><p class='muted'>These are linked public facts for human review, not conclusions or rankings.</p>
<table><thead><tr><th>Type</th><th>Summary</th><th>Subject</th><th>Entity</th><th>Evidence records</th></tr></thead><tbody>{signal_rows}</tbody></table></section>
<section><h2>Human review queue</h2>
<table><thead><tr><th>Type</th><th>Summary</th><th>Status</th><th>Evidence records</th></tr></thead><tbody>{review_rows}</tbody></table></section>
<section><h2>Recent records</h2><table><thead><tr><th>Title</th><th>Source</th><th>Class</th><th>Published</th><th>Evidence</th></tr></thead><tbody>{docs}</tbody></table></section>
<section><h2>Detected source changes</h2><table><thead><tr><th>Source</th><th>Type</th><th>URL</th><th>Detected</th></tr></thead><tbody>{changes}</tbody></table></section>
</main></body></html>"""
    (out_dir / "index.html").write_text(page, encoding="utf-8")
    (out_dir / "data.json").write_text(
        json.dumps(data, default=str, indent=2) + "\n", encoding="utf-8"
    )
