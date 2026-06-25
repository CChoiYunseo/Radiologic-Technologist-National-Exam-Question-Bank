#!/usr/bin/env python3
"""Run a controlled batch of local-Codex question generation dry runs.

The batch writes draft items and validation reports only. It never writes to
the final question bank.
"""

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

from generate_question_dry_run import (
    DEFAULT_ANSWER_EVIDENCE_VECTOR_DB,
    DEFAULT_DB,
    DEFAULT_GENERATION_SAFE_VECTOR_DB,
    DEFAULT_PACKAGES,
    DEFAULT_OUTPUT_DIR,
    OUTPUT_SCHEMA,
    build_prompt,
    evidence_for_prompt,
    load_vector_rag_ids,
    package_is_default_safe,
    package_safe_ref_errors,
    read_jsonl,
    run_codex,
    safe_slug,
    validate_item,
    write_json,
    write_text,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def batch_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def scope_label(package: dict[str, Any]) -> str:
    scope = package.get("requested_scope") or {}
    return " / ".join(scope.get(key, "") for key in ["period", "subject", "field", "area", "detail"] if scope.get(key))


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# 문제 생성 Batch Dry-Run 보고서",
        "",
        f"- 생성 시각: {report['created_at']}",
        f"- 실행 모드: {report['mode']}",
        f"- 입력 패키지: `{report['inputs']['packages']}`",
        f"- 대상 패키지 수: {report['counts']['selected_packages']}",
        f"- 생성 시도: {report['counts']['attempted']}",
        f"- Harness 통과: {report['counts']['passed']}",
        f"- Harness 실패: {report['counts']['failed']}",
        f"- 실행 오류: {report['counts']['errors']}",
        "",
        "## 결과",
    ]
    for item in report["results"]:
        lines.append(
            f"- {item['status']}: `{item['package_id']}` | {item['scope_label']} | `{item['run_dir']}`"
        )
    lines.extend(
        [
            "",
            "## 주의",
            "- 이 배치는 문제은행 저장이 아니라 생성 초안 검증입니다.",
            "- Harness 통과본도 전문가 검수 전에는 reviewed/approved로 전환하지 않습니다.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages", type=Path, default=DEFAULT_PACKAGES)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--answer-evidence-vector-db", type=Path, default=DEFAULT_ANSWER_EVIDENCE_VECTOR_DB)
    parser.add_argument("--generation-safe-vector-db", type=Path, default=DEFAULT_GENERATION_SAFE_VECTOR_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--max-evidence-chars", type=int, default=1200)
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--include-policy-risk", action="store_true", help="Include packages normally skipped by default safety filtering.")
    parser.add_argument("--allow-unsafe-package", action="store_true", help="Allow evidence refs outside generation-safe vector index. Diagnostics only.")
    parser.add_argument("--run-codex", action="store_true", help="Actually call local `codex exec`; otherwise only prepare prompts.")
    args = parser.parse_args()

    packages = read_jsonl(args.packages)
    generation_safe_ids = load_vector_rag_ids(args.generation_safe_vector_db)
    if args.include_policy_risk:
        eligible = packages
    else:
        eligible = [package for package in packages if package_is_default_safe(package)]
    selected = eligible[args.offset : args.offset + args.limit]

    bid = batch_id()
    batch_dir = args.output_dir / f"batch_{bid}_limit{args.limit}_offset{args.offset}"
    batch_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    counts = {
        "input_packages": len(packages),
        "eligible_packages": len(eligible),
        "selected_packages": len(selected),
        "attempted": 0,
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "prepare_only": 0,
    }

    conn = sqlite3.connect(args.db)
    try:
        for package in selected:
            counts["attempted"] += 1
            package_id = package.get("package_id", "package")
            run_dir = batch_dir / safe_slug(package_id)
            prompt_path = run_dir / "prompt.txt"
            package_path = run_dir / "request_package_snapshot.json"
            draft_path = run_dir / "draft_item.json"
            raw_path = run_dir / "codex_last_message.json"
            validation_path = run_dir / "validation_report.json"
            item_report_path = run_dir / "dry_run_report.json"

            result_row = {
                "package_id": package_id,
                "scope_label": scope_label(package),
                "run_dir": str(run_dir),
                "status": "prepare_only",
                "validation_summary": {},
                "error": "",
            }
            try:
                unsafe_refs = package_safe_ref_errors(package, generation_safe_ids)
                if unsafe_refs and not args.allow_unsafe_package:
                    raise ValueError(
                        "Package evidence is outside the generation-safe vector index. "
                        f"unsafe_count={len(unsafe_refs)} first_ids={unsafe_refs[:10]}"
                    )
                evidence = evidence_for_prompt(package, conn, args.max_evidence_chars)
                prompt = build_prompt(package, evidence)
                write_text(prompt_path, prompt)
                write_json(package_path, package)

                if not args.run_codex:
                    counts["prepare_only"] += 1
                    write_json(
                        item_report_path,
                        {
                            "created_at": now_iso(),
                            "mode": "prepare_only",
                            "package_id": package_id,
                            "scope_label": result_row["scope_label"],
                            "outputs": {
                                "prompt": str(prompt_path),
                                "package_snapshot": str(package_path),
                            },
                        },
                    )
                else:
                    draft_item = run_codex(prompt, OUTPUT_SCHEMA, raw_path, args.model, args.timeout)
                    write_json(draft_path, draft_item)
                    validation_report = validate_item(draft_item, conn, generation_safe_ids)
                    write_json(validation_path, validation_report)
                    result_row["validation_summary"] = validation_report["summary"]
                    if validation_report["summary"]["overall_pass"]:
                        result_row["status"] = "passed"
                        counts["passed"] += 1
                    else:
                        result_row["status"] = "failed"
                        counts["failed"] += 1
                    write_json(
                        item_report_path,
                        {
                            "created_at": now_iso(),
                            "mode": "codex_cli",
                            "package_id": package_id,
                            "scope_label": result_row["scope_label"],
                            "outputs": {
                                "prompt": str(prompt_path),
                                "package_snapshot": str(package_path),
                                "raw_codex_last_message": str(raw_path),
                                "draft_item": str(draft_path),
                                "validation_report": str(validation_path),
                            },
                            "validation_report": validation_report,
                        },
                    )
            except Exception as exc:  # Keep batch progress even if one package fails.
                result_row["status"] = "error"
                result_row["error"] = str(exc)[-2000:]
                counts["errors"] += 1
                write_json(
                    item_report_path,
                    {
                        "created_at": now_iso(),
                        "mode": "error",
                        "package_id": package_id,
                        "scope_label": result_row["scope_label"],
                        "error": result_row["error"],
                        "outputs": {
                            "prompt": str(prompt_path),
                            "package_snapshot": str(package_path),
                        },
                    },
                )
            results.append(result_row)
    finally:
        conn.close()

    report = {
        "version": "2026-06-24",
        "created_at": now_iso(),
        "mode": "codex_cli" if args.run_codex else "prepare_only",
        "inputs": {
            "packages": str(args.packages),
            "db": str(args.db),
            "answer_evidence_vector_db": str(args.answer_evidence_vector_db),
            "generation_safe_vector_db": str(args.generation_safe_vector_db),
            "output_schema": str(OUTPUT_SCHEMA),
        },
        "batch_dir": str(batch_dir),
        "counts": counts,
        "results": results,
        "policy": {
            "final_question_bank_storage": False,
            "strict_packages_only": args.packages == DEFAULT_PACKAGES,
            "policy_risk_packages_included": bool(args.include_policy_risk),
            "visual_formula_table_law_default_excluded": not args.include_policy_risk,
            "generation_safe_index_enforced": not args.allow_unsafe_package,
            "generation_safe_index_id_count": len(generation_safe_ids),
        },
    }
    passed_index = batch_dir / "passed_drafts_index.jsonl"
    failed_index = batch_dir / "failed_drafts_index.jsonl"
    error_index = batch_dir / "error_drafts_index.jsonl"
    prepare_index = batch_dir / "prepared_drafts_index.jsonl"
    index_rows = {
        "passed": [],
        "failed": [],
        "error": [],
        "prepare_only": [],
    }
    for row in results:
        run_dir = Path(row["run_dir"])
        index_row = dict(row)
        index_row["draft_item"] = str(run_dir / "draft_item.json")
        index_row["validation_report"] = str(run_dir / "validation_report.json")
        index_row["request_package_snapshot"] = str(run_dir / "request_package_snapshot.json")
        index_rows.setdefault(row["status"], []).append(index_row)
    for path, rows in [
        (passed_index, index_rows["passed"]),
        (failed_index, index_rows["failed"]),
        (error_index, index_rows["error"]),
        (prepare_index, index_rows["prepare_only"]),
    ]:
        with path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    report["outputs"] = {
        "passed_drafts_index": str(passed_index),
        "failed_drafts_index": str(failed_index),
        "error_drafts_index": str(error_index),
        "prepared_drafts_index": str(prepare_index),
    }
    report_json = batch_dir / "batch_report.json"
    report_md = batch_dir / "batch_report.md"
    write_json(report_json, report)
    write_text(report_md, markdown_report(report))
    print(json.dumps({"batch_dir": str(batch_dir), "counts": counts, "report": str(report_json)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
