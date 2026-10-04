from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb


def _rows(con: duckdb.DuckDBPyConnection, sql: str, params=None):
    return con.execute(sql, params or []).fetchall()


def _dict_rows(con: duckdb.DuckDBPyConnection, sql: str, params=None):
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def _review_map(con: duckdb.DuckDBPyConnection):
    rows = _dict_rows(
        con,
        """SELECT review_id,item_type,score,status,summary,
                  subject_entity_id,object_entity_id,evidence_json,metadata_json
           FROM review_queue""",
    )
    return {str(row["review_id"]): row for row in rows}


def _signal_map(con: duckdb.DuckDBPyConnection):
    rows = _dict_rows(
        con,
        """SELECT signal_id,signal_type,score,status,summary,
                  subject_entity_id,object_entity_id,evidence_json,metadata_json
           FROM signals""",
    )
    return {str(row["signal_id"]): row for row in rows}


def _entity_names(con: duckdb.DuckDBPyConnection):
    return {
        str(entity_id): str(name)
        for entity_id, name in _rows(con, "SELECT entity_id,canonical_name FROM entities")
    }


def _counts(con: duckdb.DuckDBPyConnection, table: str, type_col: str):
    return _rows(
        con,
        f"""SELECT {type_col},status,count(*)
            FROM {table}
            GROUP BY {type_col},status
            ORDER BY {type_col},status""",
    )


def _pretty_json(value: str | None):
    try:
        return json.dumps(json.loads(value or "{}"), sort_keys=True)
    except json.JSONDecodeError:
        return value or "{}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare review/signal state between two tracker DuckDB files."
    )
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--after", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()

    before = duckdb.connect(str(args.before), read_only=True)
    after = duckdb.connect(str(args.after), read_only=True)

    before_reviews = _review_map(before)
    after_reviews = _review_map(after)
    before_signals = _signal_map(before)
    after_signals = _signal_map(after)
    names = _entity_names(after)

    print("=== REVIEW COUNTS: BEFORE ===")
    for row in _counts(before, "review_queue", "item_type"):
        print(*row, sep=" | ")
    print("\n=== REVIEW COUNTS: AFTER ===")
    for row in _counts(after, "review_queue", "item_type"):
        print(*row, sep=" | ")

    print("\n=== SIGNAL COUNTS: BEFORE ===")
    for row in _counts(before, "signals", "signal_type"):
        print(*row, sep=" | ")
    print("\n=== SIGNAL COUNTS: AFTER ===")
    for row in _counts(after, "signals", "signal_type"):
        print(*row, sep=" | ")

    new_open_reviews = [
        row
        for key, row in after_reviews.items()
        if row["status"] == "open"
        and (key not in before_reviews or before_reviews[key]["status"] != "open")
    ]
    new_live_signals = [
        row
        for key, row in after_signals.items()
        if row["status"] in {"review", "approved"}
        and (
            key not in before_signals
            or before_signals[key]["status"] not in {"review", "approved"}
        )
    ]

    print(f"\n=== NEW/REOPENED OPEN REVIEWS ({len(new_open_reviews)}) ===")
    for row in sorted(new_open_reviews, key=lambda r: (-float(r["score"]), str(r["item_type"])))[: args.limit]:
        subject = names.get(str(row["subject_entity_id"]), row["subject_entity_id"])
        object_ = names.get(str(row["object_entity_id"]), row["object_entity_id"])
        print(
            f"{row['item_type']} | {float(row['score']):.4f} | {subject} | {object_} | {row['summary']}"
        )
        if row["item_type"] != "ENTITY_MATCH":
            print("  metadata:", _pretty_json(row["metadata_json"]))

    print(f"\n=== NEW/REOPENED ACTIVE SIGNALS ({len(new_live_signals)}) ===")
    for row in sorted(new_live_signals, key=lambda r: (-float(r["score"]), str(r["signal_type"])))[: args.limit]:
        subject = names.get(str(row["subject_entity_id"]), row["subject_entity_id"])
        object_ = names.get(str(row["object_entity_id"]), row["object_entity_id"])
        print(
            f"{row['signal_type']} | {float(row['score']):.4f} | {subject} | {object_} | {row['summary']}"
        )
        print("  metadata:", _pretty_json(row["metadata_json"]))

    before.close()
    after.close()


if __name__ == "__main__":
    main()
