from __future__ import annotations

import html
import json
from pathlib import Path

from .db import connect
from .health import ensure_health_schema


FALLBACK_EXPECTATIONS = {
    "westmorland_furness_spending": {
        "label": "Spending over £250",
        "cadence_days": 45,
        "grace_days": 20,
        "basis": "Configured monthly publication series",
    },
}


def _expectations(con) -> dict[str, dict]:
    expectations = dict(FALLBACK_EXPECTATIONS)
    for source_id, config_json in con.execute("SELECT source_id,config_json FROM sources").fetchall():
        try:
            config = json.loads(config_json or "{}")
        except json.JSONDecodeError:
            continue
        expectation = config.get("publication_expectation")
        if not isinstance(expectation, dict):
            continue
        expectations[str(source_id)] = {
            "label": str(expectation.get("label") or expectation.get("dataset") or source_id),
            "cadence_days": int(expectation.get("cadence_days", 90)),
            "grace_days": int(expectation.get("grace_days", 30)),
            "basis": str(
                expectation.get("basis")
                or "Configured publication expectation from official source series"
            ),
        }
    return expectations


def publication_status_rows(con) -> list[dict]:
    ensure_health_schema(con)
    rows: list[dict] = []
    for source_id, expectation in sorted(_expectations(con).items()):
        health = con.execute(
            """SELECT status,http_status,started_at,finished_at,error_message
               FROM latest_source_health WHERE source_id=?""",
            [source_id],
        ).fetchone()
        observation = con.execute(
            """SELECT max(retrieved_at),
                      date_diff('day', max(retrieved_at), now())
               FROM observations WHERE source_id=?""",
            [source_id],
        ).fetchone()
        last_snapshot = observation[0] if observation else None
        age_days = observation[1] if observation and observation[0] is not None else None
        if health is None:
            status = "not_checked"
            http_status = None
            last_checked = None
            error_message = None
        else:
            run_status, http_status, started_at, finished_at, error_message = health
            last_checked = finished_at or started_at
            if run_status in {"blocked", "partial_blocked"}:
                status = "blocked" if run_status == "blocked" else "partial"
            elif run_status == "missing":
                status = "missing"
            elif run_status in {
                "rate_limited",
                "http_error",
                "network_error",
                "error",
                "configuration_error",
            }:
                status = "error"
            elif run_status in {"partial", "partial_extraction"}:
                status = "partial"
            elif last_snapshot is None:
                status = "missing"
            elif age_days is not None and age_days > (
                int(expectation["cadence_days"]) + int(expectation["grace_days"])
            ):
                status = "stale"
            else:
                status = "present"
        rows.append(
            {
                "source_id": source_id,
                "dataset": expectation["label"],
                "status": status,
                "cadence_days": expectation["cadence_days"],
                "grace_days": expectation["grace_days"],
                "basis": expectation["basis"],
                "last_checked": last_checked,
                "last_snapshot": last_snapshot,
                "age_days": age_days,
                "http_status": http_status,
                "error_message": error_message,
            }
        )
    return rows


def _link_from_index(out_dir: Path) -> None:
    index_path = out_dir / "index.html"
    if not index_path.exists():
        return
    page = index_path.read_text(encoding="utf-8")
    if "publication-status.html" in page:
        return
    marker = "<a class='button' href='api/index.json'>API</a>"
    link = "<a class='button' href='publication-status.html'>Publication status</a>"
    if marker in page:
        page = page.replace(marker, marker + link, 1)
        index_path.write_text(page, encoding="utf-8")


def build_publication_status(db_path: Path, out_dir: Path) -> None:
    con = connect(db_path)
    rows = publication_status_rows(con)
    con.close()

    api_dir = out_dir / "api"
    api_dir.mkdir(parents=True, exist_ok=True)
    (api_dir / "publication-status.json").write_text(
        json.dumps(rows, default=str, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    body_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(row['dataset']))}</td>"
        f"<td><code>{html.escape(str(row['source_id']))}</code></td>"
        f"<td>{html.escape(str(row['status']))}</td>"
        f"<td>{html.escape(str(row['last_snapshot'] or ''))}</td>"
        f"<td>{html.escape(str(row['last_checked'] or ''))}</td>"
        f"<td>{html.escape(str(row['http_status'] or ''))}</td>"
        f"<td>{html.escape(str(row['basis']))}</td>"
        "</tr>"
        for row in rows
    )
    page = f"""<!doctype html>
<html lang='en'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Publication status — Cumbria Public Records Evidence Tracker</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:1200px;margin:auto;padding:2rem;color:#171717}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:.6rem;border-bottom:1px solid #ddd;text-align:left;vertical-align:top}}
code{{background:#eef1f4;padding:.1rem .25rem;border-radius:4px}}
.note{{background:#f6f7f8;border:1px solid #ddd;border-radius:10px;padding:1rem}}
</style>
</head>
<body>
<p><a href='index.html'>← Main tracker</a></p>
<h1>Source health & publication status</h1>
<p class='note'>This page reports whether configured public datasets were observed by the collector and how recently they were retrieved. A stale, missing, blocked, partial or error state is a collection/publication-status signal only; it is not a finding of legal non-compliance or wrongdoing.</p>
<table>
<thead><tr><th>Dataset</th><th>Source</th><th>Status</th><th>Last snapshot</th><th>Last checked</th><th>HTTP</th><th>Expectation basis</th></tr></thead>
<tbody>{body_rows}</tbody>
</table>
</body>
</html>"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "publication-status.html").write_text(page, encoding="utf-8")
    _link_from_index(out_dir)
