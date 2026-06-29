#!/usr/bin/env python3
"""Rebalance answer positions in the expert-review candidate store.

This updates only option order and the answer index for pending candidates.
Question stems, explanations, evidence, validation records, and source paths
are preserved. The operation is deterministic and writes an audit report.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from question_option_randomizer import normalize_options, parse_answer, reorder_item_answer_position


DEFAULT_DB = PROJECT_ROOT / "resources/generated/question_bank_candidates/question_bank_candidates.sqlite"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "resources/generated/question_bank_candidates/answer_position_rebalance"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def loads_json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return default


def dumps_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def distribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_answer = Counter(str(row.get("answer") or "") for row in rows)
    by_subject = Counter(str(row.get("subject") or "") for row in rows)
    by_subject_answer: dict[str, Counter[str]] = {}
    for row in rows:
        subject = str(row.get("subject") or "")
        by_subject_answer.setdefault(subject, Counter())[str(row.get("answer") or "")] += 1
    return {
        "total": len(rows),
        "by_answer": dict(sorted(by_answer.items())),
        "by_subject": dict(sorted(by_subject.items())),
        "by_subject_answer": {
            subject: dict(sorted(counter.items())) for subject, counter in sorted(by_subject_answer.items())
        },
    }


def fetch_rows(conn: sqlite3.Connection, status: str) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, status, source_stage, subject, field, area, detail,
               stem, option_1, option_2, option_3, option_4, option_5,
               options_json, answer, candidate_payload_json
        FROM question_bank_candidate
        WHERE status = ?
        ORDER BY subject, field, area, detail, source_stage, id
        """,
        (status,),
    ).fetchall()
    return [dict(row) for row in rows]


def current_options(row: dict[str, Any]) -> list[str] | None:
    options = normalize_options(loads_json(row.get("options_json"), []))
    if options:
        return options
    values = [row.get(f"option_{index}") or "" for index in range(1, 6)]
    return normalize_options(values)


def update_payload(payload: dict[str, Any], item: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    updated = dict(payload)
    draft = updated.get("draft_item")
    if isinstance(draft, dict):
        draft = dict(draft)
        draft["options"] = item["options"]
        draft["answer"] = item["answer"]
        updated["draft_item"] = draft
    updated["answer_position_randomization"] = info
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--status", default="pending_expert_review")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.db.exists():
        raise FileNotFoundError(args.db)

    rid = run_id()
    report_dir = args.report_dir / f"run_{rid}"
    report_dir.mkdir(parents=True, exist_ok=True)
    audit_path = report_dir / "answer_position_rebalance_audit.jsonl"
    report_path = report_dir / "answer_position_rebalance_report.json"

    conn = sqlite3.connect(args.db)
    try:
        rows = fetch_rows(conn, args.status)
        before = distribution(rows)
        updates: list[dict[str, Any]] = []
        audit_rows: list[dict[str, Any]] = []
        target_cycle = [1, 2, 3, 4, 5]
        valid_index = 0
        for row in rows:
            options = current_options(row)
            answer = parse_answer(row.get("answer"))
            if options is None or answer is None:
                audit_rows.append(
                    {
                        "candidate_id": row["id"],
                        "status": "skipped",
                        "reason": "invalid_options_or_answer",
                        "answer": row.get("answer"),
                    }
                )
                continue
            target = target_cycle[valid_index % len(target_cycle)]
            valid_index += 1
            item = {
                "period": "",
                "subject": row.get("subject") or "",
                "field": row.get("field") or "",
                "area": row.get("area") or "",
                "detail": row.get("detail") or "",
                "stem": row.get("stem") or "",
                "options": options,
                "answer": answer,
            }
            updated_item, info = reorder_item_answer_position(
                item,
                target_answer=target,
                seed_parts=[row["id"], row.get("source_stage"), row.get("stem")],
            )
            payload = loads_json(row.get("candidate_payload_json"), {})
            updated_payload = update_payload(payload if isinstance(payload, dict) else {}, updated_item, info)
            update_row = {
                "id": row["id"],
                "option_1": updated_item["options"][0],
                "option_2": updated_item["options"][1],
                "option_3": updated_item["options"][2],
                "option_4": updated_item["options"][3],
                "option_5": updated_item["options"][4],
                "options_json": dumps_json(updated_item["options"]),
                "answer": int(updated_item["answer"]),
                "candidate_payload_json": dumps_json(updated_payload),
                "updated": now_iso(),
            }
            updates.append(update_row)
            audit_rows.append(
                {
                    "candidate_id": row["id"],
                    "status": "changed" if info["changed"] else "unchanged",
                    "subject": row.get("subject") or "",
                    "source_stage": row.get("source_stage") or "",
                    "original_answer": info["original_answer"],
                    "target_answer": info["target_answer"],
                }
            )

        after_rows = []
        for row, update in zip([r for r in rows if current_options(r) is not None and parse_answer(r.get("answer"))], updates):
            after_row = dict(row)
            after_row["answer"] = update["answer"]
            after_rows.append(after_row)
        after = distribution(after_rows)

        if not args.dry_run:
            with conn:
                conn.executemany(
                    """
                    UPDATE question_bank_candidate
                    SET option_1 = :option_1,
                        option_2 = :option_2,
                        option_3 = :option_3,
                        option_4 = :option_4,
                        option_5 = :option_5,
                        options_json = :options_json,
                        answer = :answer,
                        candidate_payload_json = :candidate_payload_json,
                        updated = :updated
                    WHERE id = :id
                    """,
                    updates,
                )
            after = distribution(fetch_rows(conn, args.status))
    finally:
        conn.close()

    with audit_path.open("w", encoding="utf-8") as f:
        for row in audit_rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    report = {
        "created_at": now_iso(),
        "db": str(args.db),
        "dry_run": args.dry_run,
        "status_filter": args.status,
        "counts": {
            "input_rows": len(rows),
            "updated_rows": len(updates) if not args.dry_run else 0,
            "eligible_rows": len(updates),
            "skipped_rows": len(rows) - len(updates),
        },
        "before": before,
        "after": after,
        "outputs": {
            "audit_jsonl": str(audit_path),
            "report_json": str(report_path),
        },
        "policy": {
            "changed_fields": ["option_1..option_5", "options_json", "answer", "candidate_payload_json", "updated"],
            "stem_explanation_evidence_unchanged": True,
            "final_question_bank_approved": False,
        },
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
