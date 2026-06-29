#!/usr/bin/env python3
"""Append LLM-pass drafts into the expert-review candidate store."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from build_question_bank_candidate_store import (  # noqa: E402
    DEFAULT_OUTPUT_DIR,
    build_candidate,
    insert_rows,
    read_json,
    read_jsonl,
    sqlite_schema,
    write_json,
    write_jsonl,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_validation_package(row: dict[str, Any]) -> Path:
    direct = row.get("original_validation_package_snapshot") or row.get("validation_package_snapshot")
    if direct:
        path = Path(direct)
        if path.exists():
            return path
    run_dir = row.get("run_dir")
    if run_dir:
        path = Path(run_dir) / "validation_package_snapshot.json"
        if path.exists():
            return path
    raise FileNotFoundError(f"validation_package_snapshot not found for {row.get('validation_package_id')}")


def normalize_row(row: dict[str, Any], package_path: Path) -> dict[str, Any]:
    normalized = dict(row)
    normalized["run_dir"] = str(package_path.parent)
    normalized["llm_validation_result"] = row.get("llm_validation_result") or str(
        package_path.parent / "llm_validation_result.json"
    )
    normalized["validation_run_report"] = row.get("validation_run_report") or str(
        package_path.parent / "validation_run_report.json"
    )
    return normalized


def existing_candidate_meta(conn: sqlite3.Connection, candidate_id: str) -> dict[str, Any] | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT status, created FROM question_bank_candidate WHERE id = ?",
        (candidate_id,),
    ).fetchone()
    return dict(row) if row else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--source-stage", default="subject_quota_llm_pass")
    parser.add_argument("--import-label", default="")
    args = parser.parse_args()

    db_path = args.output_dir / "question_bank_candidates.sqlite"
    if not db_path.exists():
        raise FileNotFoundError(f"candidate store not found: {db_path}")

    rows = read_jsonl(args.queue)
    candidates: list[dict[str, Any]] = []
    evidence_records: list[dict[str, Any]] = []
    validation_records: list[dict[str, Any]] = []
    imported: list[dict[str, Any]] = []

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(sqlite_schema())
        for row in rows:
            package_path = resolve_validation_package(row)
            normalized = normalize_row(row, package_path)
            validation_package = read_json(package_path)
            llm_result = read_json(Path(normalized["llm_validation_result"]))
            candidate, evidence, validations = build_candidate(
                normalized,
                args.source_stage,
                validation_package,
                llm_result,
                None,
            )
            previous = existing_candidate_meta(conn, candidate["id"])
            if previous:
                candidate["status"] = previous["status"]
                candidate["created"] = previous["created"]
            policy = candidate["candidate_payload_json"].setdefault("policy", {})
            policy["source_stage"] = args.source_stage
            policy["import_label"] = args.import_label or args.source_stage
            policy["imported_at"] = now_iso()
            candidate["source_paths_json"]["import_queue"] = str(args.queue)

            candidates.append(candidate)
            evidence_records.extend(evidence)
            validation_records.extend(validations)
            imported.append(
                {
                    "candidate_id": candidate["id"],
                    "package_id": candidate["package_id"],
                    "validation_package_id": candidate["validation_package_id"],
                    "status": candidate["status"],
                    "source_stage": candidate["source_stage"],
                    "subject": candidate["subject"],
                    "stem": candidate["stem"],
                }
            )

        insert_rows(conn, "question_bank_candidate", candidates)
        insert_rows(conn, "question_bank_candidate_evidence", evidence_records)
        insert_rows(conn, "question_bank_candidate_validation", validation_records)
        conn.commit()
    finally:
        conn.close()

    report_dir = args.output_dir / "imports" / (args.import_label or args.source_stage)
    report_dir.mkdir(parents=True, exist_ok=True)
    imported_jsonl = report_dir / "question_bank_candidates_imported.jsonl"
    report_json = report_dir / "question_bank_candidates_import_report.json"
    write_jsonl(imported_jsonl, imported)
    report = {
        "created_at": now_iso(),
        "input_queue": str(args.queue),
        "sqlite_db": str(db_path),
        "source_stage": args.source_stage,
        "import_label": args.import_label or args.source_stage,
        "counts": {
            "input_rows": len(rows),
            "imported_candidates": len(candidates),
            "evidence_records": len(evidence_records),
            "validation_records": len(validation_records),
        },
        "outputs": {
            "imported_jsonl": str(imported_jsonl),
            "json_report": str(report_json),
        },
        "policy": {
            "candidate_status": "pending_expert_review unless existing status is already set",
            "final_question_bank_approved": False,
            "source_full_text_stored": False,
        },
    }
    write_json(report_json, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
