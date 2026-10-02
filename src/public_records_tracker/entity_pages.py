from __future__ import annotations

import html
import json
from pathlib import Path

from .db import connect
from .structured import ensure_structured_schema


def _entity_rows(con) -> list[dict]:
    rows = con.execute(
        """SELECT e.entity_id,e.entity_type,e.canonical_name,
                  count(DISTINCT f.fact_id) AS fact_count
           FROM entities e
           JOIN latest_facts f
             ON f.subject_entity_id=e.entity_id OR f.object_entity_id=e.entity_id
           GROUP BY e.entity_id,e.entity_type,e.canonical_name
           HAVING count(DISTINCT f.fact_id) > 0
           ORDER BY lower(e.canonical_name), e.entity_type"""
    ).fetchall()
    return [
        {
            "entity_id": row[0],
            "entity_type": row[1],
            "name": row[2],
            "fact_count": int(row[3]),
        }
        for row in rows
    ]


def _timeline(con, entity_id: str) -> list[dict]:
    rows = con.execute(
        """SELECT f.fact_id,f.fact_type,f.predicate,f.value_text,f.locator,
                  f.evidence_class,d.title,d.canonical_url,d.published_at,
                  s.retrieved_at,s.sha256,
                  f.subject_entity_id,f.object_entity_id,
                  es.canonical_name,eo.canonical_name
           FROM latest_facts f
           JOIN documents d ON d.document_id=f.document_id
           JOIN snapshots s ON s.snapshot_id=f.snapshot_id
           LEFT JOIN entities es ON es.entity_id=f.subject_entity_id
           LEFT JOIN entities eo ON eo.entity_id=f.object_entity_id
           WHERE f.subject_entity_id=? OR f.object_entity_id=?
           ORDER BY coalesce(try_cast(d.published_at AS TIMESTAMP), s.retrieved_at) DESC,
                    f.fact_type,f.fact_id""",
        [entity_id, entity_id],
    ).fetchall()
    out = []
    for row in rows:
        related = None
        if row[11] == entity_id and row[12] and row[12] != entity_id:
            related = row[14]
        elif row[12] == entity_id and row[11] and row[11] != entity_id:
            related = row[13]
        out.append(
            {
                "fact_id": row[0],
                "fact_type": row[1],
                "predicate": row[2],
                "value": row[3],
                "locator": row[4],
                "evidence_class": row[5],
                "document_title": row[6],
                "source_url": row[7],
                "published_at": row[8],
                "retrieved_at": row[9],
                "sha256": row[10],
                "related_entity": related,
            }
        )
    return out


def build_entity_pages(db_path: Path, out_dir: Path) -> None:
    con = connect(db_path)
    ensure_structured_schema(con)
    entities = _entity_rows(con)
    entity_dir = out_dir / "entities"
    api_dir = out_dir / "api" / "entities"
    entity_dir.mkdir(parents=True, exist_ok=True)
    api_dir.mkdir(parents=True, exist_ok=True)

    index_items = []
    api_index = []
    for entity in entities:
        timeline = _timeline(con, entity["entity_id"])
        payload = {**entity, "timeline": timeline}
        (api_dir / f"{entity['entity_id']}.json").write_text(
            json.dumps(payload, default=str, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        api_index.append(entity)
        rows = "".join(
            "<tr>"
            f"<td>{html.escape(str(item['published_at'] or item['retrieved_at'] or ''))}</td>"
            f"<td>{html.escape(str(item['fact_type']))}</td>"
            f"<td>{html.escape(str(item['predicate']))}</td>"
            f"<td>{html.escape(str(item['value'] or item['related_entity'] or ''))}</td>"
            f"<td>{html.escape(str(item['locator'] or ''))}</td>"
            f"<td><a rel='noreferrer' href='{html.escape(str(item['source_url']), quote=True)}'>source</a></td>"
            f"<td><code>{html.escape(str(item['sha256']))}</code></td>"
            "</tr>"
            for item in timeline
        )
        page = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{html.escape(entity['name'])} — Cumbria Public Records</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1300px;margin:auto;padding:2rem;color:#171717}}table{{width:100%;border-collapse:collapse}}th,td{{padding:.55rem;border-bottom:1px solid #ddd;text-align:left;vertical-align:top}}code{{font-size:.78rem;overflow-wrap:anywhere}}.note{{background:#f6f7f8;border:1px solid #ddd;border-radius:10px;padding:1rem}}</style>
</head><body><p><a href='../entities.html'>← Entities</a></p>
<h1>{html.escape(entity['name'])}</h1><p>{html.escape(entity['entity_type'])}</p>
<p class='note'>This timeline is assembled from linked public-record facts. Sequence or association does not by itself establish wrongdoing, motive, or a conflict of interest. Follow the cited source and locator to verify each entry.</p>
<table><thead><tr><th>Date</th><th>Fact</th><th>Relationship</th><th>Value / related entity</th><th>Locator</th><th>Evidence</th><th>SHA-256</th></tr></thead><tbody>{rows}</tbody></table>
</body></html>"""
        (entity_dir / f"{entity['entity_id']}.html").write_text(page, encoding="utf-8")
        index_items.append(
            f"<tr><td><a href='entities/{entity['entity_id']}.html'>{html.escape(entity['name'])}</a></td>"
            f"<td>{html.escape(entity['entity_type'])}</td><td>{entity['fact_count']}</td></tr>"
        )

    con.close()
    (out_dir / "api" / "entities.json").write_text(
        json.dumps(api_index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    index_page = """<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Entities — Cumbria Public Records</title>
<style>body{font-family:system-ui,sans-serif;max-width:1100px;margin:auto;padding:2rem;color:#171717}table{width:100%;border-collapse:collapse}th,td{padding:.6rem;border-bottom:1px solid #ddd;text-align:left}</style>
</head><body><p><a href='index.html'>← Main tracker</a></p><h1>Entities</h1>
<p>People, organisations, suppliers, committees and other entities appearing in structured public records.</p>
<table><thead><tr><th>Name</th><th>Type</th><th>Current facts</th></tr></thead><tbody>""" + "".join(index_items) + "</tbody></table></body></html>"
    (out_dir / "entities.html").write_text(index_page, encoding="utf-8")
