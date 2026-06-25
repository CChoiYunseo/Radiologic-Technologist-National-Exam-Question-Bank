#!/usr/bin/env python3
"""Process LLM second-pass verdicts into follow-up work products.

Pass items are queued for expert review, revise items can be rewritten as new
drafts, and reject items are converted into re-selection worklist entries.
Nothing is written to the final question bank.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LLM_RUN_DIR = (
    PROJECT_ROOT
    / "resources/generated/review_candidates/llm_secondary_validation_runs/run_20260624T074838Z_limitall_offset0"
)
DEFAULT_DB = PROJECT_ROOT / "resources/extracted/rag_search_index_text_bm25/rag_text_bm25.sqlite"
DEFAULT_OUTPUT_SCHEMA = PROJECT_ROOT / "resources/rules/generated_question_output_schema.json"
REQUIRED_CHECKS = [
    "scope_alignment",
    "learning_objective_alignment",
    "evidence_grounding",
    "answer_uniqueness",
    "distractor_quality",
    "explanation_quality",
    "copyright_risk",
    "korean_item_style",
    "hold_material_contamination",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        value = json.load(f)
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Expected JSON object at {path}:{line_no}")
            rows.append(value)
    return rows


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", value).strip("_")
    return slug[:100] or "revision"


def extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("LLM output is not a JSON object")
    return value


def run_codex(prompt: str, schema: Path, raw_output: Path, model: str, timeout: int) -> dict[str, Any]:
    raw_output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "codex",
        "exec",
        "--cd",
        str(PROJECT_ROOT),
        "--sandbox",
        "read-only",
        "--ephemeral",
        "--output-schema",
        str(schema),
        "--output-last-message",
        str(raw_output),
    ]
    if model:
        cmd.extend(["--model", model])
    cmd.append("-")
    env = os.environ.copy()
    env.setdefault("TERM", "xterm-256color")
    env.setdefault("NO_COLOR", "1")
    result = subprocess.run(
        cmd,
        input=prompt,
        text=True,
        capture_output=True,
        timeout=timeout,
        cwd=str(PROJECT_ROOT),
        env=env,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "codex exec failed\n"
            f"returncode={result.returncode}\n"
            f"stdout={result.stdout[-2000:]}\n"
            f"stderr={result.stderr[-2000:]}"
        )
    return extract_json_object(raw_output.read_text(encoding="utf-8"))


def validate_item(item: dict[str, Any], conn: sqlite3.Connection) -> dict[str, Any]:
    import sys

    scripts_dir = PROJECT_ROOT / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from validate_rule_based_generation_harness import validate_payload

    return validate_payload({"mode": "generated_item", "item": item}, conn)


def check_summary(llm_result: dict[str, Any]) -> dict[str, Any]:
    checks = llm_result.get("checks") or {}
    revise_or_reject = []
    for check_id in REQUIRED_CHECKS:
        check = checks.get(check_id) or {}
        if check.get("verdict") in {"revise", "reject"}:
            revise_or_reject.append(
                {
                    "check_id": check_id,
                    "verdict": check.get("verdict"),
                    "reason": check.get("reason", ""),
                }
            )
    return {
        "overall_verdict": llm_result.get("overall_verdict"),
        "revision_required": llm_result.get("revision_required"),
        "notes": llm_result.get("notes", ""),
        "revise_or_reject_checks": revise_or_reject,
    }


def build_revise_package(row: dict[str, Any]) -> dict[str, Any]:
    validation_package = read_json(Path(row["run_dir"]) / "validation_package_snapshot.json")
    llm_result = read_json(Path(row["llm_validation_result"]))
    draft_item = validation_package.get("draft_item") or {}
    return {
        "revision_package_id": f"rev_{row['validation_package_id']}",
        "validation_package_id": row["validation_package_id"],
        "review_candidate_id": row["review_candidate_id"],
        "package_id": row["package_id"],
        "status": "revision_requested",
        "requested_scope": validation_package.get("requested_scope") or {},
        "recommended_generation_settings": validation_package.get("recommended_generation_settings") or {},
        "original_draft_item": draft_item,
        "evidence_for_review": validation_package.get("evidence_for_review") or [],
        "llm_secondary_feedback": check_summary(llm_result),
        "fixed_metadata": {
            "period": draft_item.get("period"),
            "subject": draft_item.get("subject"),
            "field": draft_item.get("field"),
            "area": draft_item.get("area"),
            "detail": draft_item.get("detail"),
            "scope_id": draft_item.get("scope_id"),
            "question_type": draft_item.get("question_type"),
            "competency_type": draft_item.get("competency_type"),
            "difficulty": draft_item.get("difficulty"),
            "evidence_refs": draft_item.get("evidence_refs") or [],
            "source_chunks": draft_item.get("source_chunks") or [],
        },
    }


def build_prompt(package: dict[str, Any]) -> str:
    payload = {
        "task": "LLM 2차 검증에서 revise 판정을 받은 방사선사 국가고시 1·2교시 문항 초안 재작성",
        "fixed_metadata": package["fixed_metadata"],
        "requested_scope": package.get("requested_scope"),
        "recommended_generation_settings": package.get("recommended_generation_settings"),
        "llm_secondary_feedback": package.get("llm_secondary_feedback"),
        "original_draft_item": package.get("original_draft_item"),
        "evidence_for_review": package.get("evidence_for_review"),
        "required_output_state": {
            "validation_status": "draft",
            "reviewer_agent_results": {
                "scope": "pending",
                "grounding": "pending",
                "uniqueness": "pending",
                "grammar": "pending",
                "copyright": "pending",
            },
            "final_judge": "pending_rule_harness",
            "status": "generated",
        },
    }
    rules = [
        "JSON object만 출력한다. Markdown 코드블록을 쓰지 않는다.",
        "문항 줄기, 보기 5개, 해설, 오답 구성 설명을 새 문장으로 재작성한다.",
        "전공서 발췌문이나 기존 초안 문장을 그대로 복사하거나 가깝게 재서술하지 않는다.",
        "RAG 근거는 정답 판단 근거로만 사용한다.",
        "근거에 없는 법규 조문, 수치 기준, 공식, 표·그림 내용은 추가하지 않는다.",
        "정답은 반드시 하나만 가능해야 한다.",
        "보기 5개는 길이와 문체를 비슷하게 맞추고, 서로 겹치지 않게 한다.",
        "해설에는 정답 근거와 주요 오답 배제 이유를 간결하게 포함한다.",
        "period, subject, field, area, detail, scope_id, question_type, competency_type, difficulty, evidence_refs, source_chunks는 fixed_metadata 값을 유지한다.",
        "learning_objective_id는 feedback에서 현재 ID가 부적절하다고 한 경우 recommended_generation_settings의 더 적절한 후보 ID로 바꿀 수 있다.",
        "validation_status, reviewer_agent_results, final_judge, status는 required_output_state 값을 사용한다.",
    ]
    return (
        "너는 보건의료 국가시험 문항 재작성자다.\n"
        "아래 규칙을 반드시 따른다.\n"
        + "\n".join(f"- {rule}" for rule in rules)
        + "\n\n입력 JSON:\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n\n위 입력을 반영한 재작성 문항 JSON object만 출력하라.\n"
    )


def build_pass_queue(pass_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    queue = []
    for row in pass_rows:
        validation_package = read_json(Path(row["run_dir"]) / "validation_package_snapshot.json")
        queue.append(
            {
                "review_candidate_id": row["review_candidate_id"],
                "validation_package_id": row["validation_package_id"],
                "package_id": row["package_id"],
                "status": "llm_secondary_pass_pending_expert_review",
                "scope": {
                    "period": row.get("period"),
                    "subject": row.get("subject"),
                    "field": row.get("field"),
                    "area": row.get("area"),
                    "detail": row.get("detail"),
                    "scope_id": row.get("scope_id"),
                },
                "learning_objective_id": row.get("learning_objective_id"),
                "question_type": row.get("question_type"),
                "difficulty": row.get("difficulty"),
                "llm_validation_result": row.get("llm_validation_result"),
                "original_validation_package_snapshot": str(Path(row["run_dir"]) / "validation_package_snapshot.json"),
                "evidence_count": len(validation_package.get("evidence_for_review") or []),
            }
        )
    return queue


def build_reject_worklist(reject_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    worklist = []
    for row in reject_rows:
        llm_result = read_json(Path(row["llm_validation_result"]))
        worklist.append(
            {
                "review_candidate_id": row["review_candidate_id"],
                "validation_package_id": row["validation_package_id"],
                "package_id": row["package_id"],
                "status": "rejected_reselect_scope_learning_objective_or_evidence",
                "scope": {
                    "period": row.get("period"),
                    "subject": row.get("subject"),
                    "field": row.get("field"),
                    "area": row.get("area"),
                    "detail": row.get("detail"),
                    "scope_id": row.get("scope_id"),
                },
                "learning_objective_id": row.get("learning_objective_id"),
                "llm_validation_result": row.get("llm_validation_result"),
                "rejection_summary": check_summary(llm_result),
                "recommended_next_action": "출제범위, 학습목표, 근거 chunk를 재선정한 뒤 새 요청 패키지부터 다시 생성",
            }
        )
    return worklist


def build_markdown_report(report: dict[str, Any]) -> str:
    counts = report["counts"]
    lines = [
        "# LLM 2차 판정 후속 처리 보고서",
        "",
        f"- 생성 시각: {report['created_at']}",
        f"- pass 전문가 검수 대기: {counts['pass_queue']}",
        f"- revise 재작성 대상: {counts['revision_packages']}",
        f"- reject 재선정 대상: {counts['reject_worklist']}",
        f"- 재작성 실행: {report['mode']}",
        f"- 재작성 완료: {counts['revision_attempted']}",
        f"- 재작성 Harness 통과: {counts['revision_harness_passed']}",
        f"- 재작성 Harness 실패: {counts['revision_harness_failed']}",
        f"- 재작성 오류: {counts['revision_errors']}",
        "",
        "## 산출물",
    ]
    for label, path in report["outputs"].items():
        lines.append(f"- {label}: `{path}`")
    lines.extend(
        [
            "",
            "## 주의",
            "- pass 항목도 전문가 검수 전에는 최종 승인 문항이 아닙니다.",
            "- reject 항목은 재작성하지 않았고 근거 재선정 대상으로 분리했습니다.",
            "- 재작성 항목은 다시 LLM 2차 검증을 받아야 합니다.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llm-run-dir", type=Path, default=DEFAULT_LLM_RUN_DIR)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-schema", type=Path, default=DEFAULT_OUTPUT_SCHEMA)
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--run-codex", action="store_true")
    args = parser.parse_args()

    pass_rows = read_jsonl(args.llm_run_dir / "llm_secondary_pass_index.jsonl")
    revise_rows = read_jsonl(args.llm_run_dir / "llm_secondary_revise_index.jsonl")
    reject_rows = read_jsonl(args.llm_run_dir / "llm_secondary_reject_index.jsonl")

    output_dir = args.llm_run_dir / "verdict_followup"
    revised_dir = output_dir / "revised_drafts"
    output_dir.mkdir(parents=True, exist_ok=True)

    pass_queue = build_pass_queue(pass_rows)
    reject_worklist = build_reject_worklist(reject_rows)
    revise_packages = [build_revise_package(row) for row in revise_rows]

    pass_queue_path = output_dir / "expert_review_queue_from_llm_pass.jsonl"
    reject_worklist_path = output_dir / "reject_reselection_worklist.jsonl"
    revise_packages_path = output_dir / "revision_request_packages.jsonl"
    write_jsonl(pass_queue_path, pass_queue)
    write_jsonl(reject_worklist_path, reject_worklist)
    write_jsonl(revise_packages_path, revise_packages)

    revision_results: list[dict[str, Any]] = []
    if args.run_codex:
        conn = sqlite3.connect(args.db)
        try:
            for package in revise_packages:
                item_dir = revised_dir / safe_slug(package["revision_package_id"])
                prompt_path = item_dir / "revision_prompt.txt"
                package_path = item_dir / "revision_package_snapshot.json"
                raw_path = item_dir / "codex_last_message.json"
                draft_path = item_dir / "revised_draft_item.json"
                harness_path = item_dir / "revision_harness_report.json"
                run_report_path = item_dir / "revision_run_report.json"
                write_text(prompt_path, build_prompt(package))
                write_json(package_path, package)
                if draft_path.exists() and harness_path.exists():
                    harness_report = read_json(harness_path)
                    summary = harness_report.get("summary") or {}
                    revision_results.append(
                        {
                            "revision_package_id": package["revision_package_id"],
                            "validation_package_id": package["validation_package_id"],
                            "package_id": package["package_id"],
                            "status": "completed",
                            "revised_draft_item": str(draft_path),
                            "revision_harness_report": str(harness_path),
                            "harness_overall_pass": summary.get("overall_pass"),
                            "harness_error_count": summary.get("error_count"),
                            "harness_warning_count": summary.get("warning_count"),
                        }
                    )
                    continue
                try:
                    draft_item = run_codex(
                        build_prompt(package),
                        args.output_schema,
                        raw_path,
                        args.model,
                        args.timeout,
                    )
                    write_json(draft_path, draft_item)
                    harness_report = validate_item(draft_item, conn)
                    write_json(harness_path, harness_report)
                    summary = harness_report.get("summary") or {}
                    row = {
                        "revision_package_id": package["revision_package_id"],
                        "validation_package_id": package["validation_package_id"],
                        "review_candidate_id": package["review_candidate_id"],
                        "package_id": package["package_id"],
                        "status": "completed",
                        "revised_draft_item": str(draft_path),
                        "revision_harness_report": str(harness_path),
                        "revision_run_report": str(run_report_path),
                        "harness_overall_pass": summary.get("overall_pass"),
                        "harness_error_count": summary.get("error_count"),
                        "harness_warning_count": summary.get("warning_count"),
                    }
                    write_json(
                        run_report_path,
                        {
                            "created_at": now_iso(),
                            "mode": "codex_cli",
                            "revision_package_id": package["revision_package_id"],
                            "outputs": {
                                "revision_prompt": str(prompt_path),
                                "revision_package_snapshot": str(package_path),
                                "raw_codex_last_message": str(raw_path),
                                "revised_draft_item": str(draft_path),
                                "revision_harness_report": str(harness_path),
                            },
                            "harness_summary": summary,
                        },
                    )
                    revision_results.append(row)
                except Exception as exc:
                    row = {
                        "revision_package_id": package["revision_package_id"],
                        "validation_package_id": package["validation_package_id"],
                        "review_candidate_id": package["review_candidate_id"],
                        "package_id": package["package_id"],
                        "status": "error",
                        "error": str(exc)[-3000:],
                        "revision_run_report": str(run_report_path),
                    }
                    write_json(
                        run_report_path,
                        {
                            "created_at": now_iso(),
                            "mode": "error",
                            "revision_package_id": package["revision_package_id"],
                            "error": row["error"],
                            "outputs": {
                                "revision_prompt": str(prompt_path),
                                "revision_package_snapshot": str(package_path),
                            },
                        },
                    )
                    revision_results.append(row)
        finally:
            conn.close()
    else:
        for package in revise_packages:
            item_dir = revised_dir / safe_slug(package["revision_package_id"])
            write_text(item_dir / "revision_prompt.txt", build_prompt(package))
            write_json(item_dir / "revision_package_snapshot.json", package)
            revision_results.append(
                {
                    "revision_package_id": package["revision_package_id"],
                    "validation_package_id": package["validation_package_id"],
                    "review_candidate_id": package["review_candidate_id"],
                    "package_id": package["package_id"],
                    "status": "prepared",
                    "revision_prompt": str(item_dir / "revision_prompt.txt"),
                    "revision_package_snapshot": str(item_dir / "revision_package_snapshot.json"),
                }
            )

    revision_results_path = output_dir / "revision_results.jsonl"
    revision_passed_path = output_dir / "revision_harness_passed_index.jsonl"
    revision_failed_path = output_dir / "revision_harness_failed_index.jsonl"
    revision_error_path = output_dir / "revision_error_index.jsonl"
    report_json_path = output_dir / "verdict_followup_report.json"
    report_md_path = output_dir / "verdict_followup_report.md"
    write_jsonl(revision_results_path, revision_results)
    write_jsonl(
        revision_passed_path,
        [row for row in revision_results if row.get("harness_overall_pass") is True],
    )
    write_jsonl(
        revision_failed_path,
        [row for row in revision_results if row.get("harness_overall_pass") is False],
    )
    write_jsonl(revision_error_path, [row for row in revision_results if row.get("status") == "error"])

    check_counter: dict[str, Counter[str]] = defaultdict(Counter)
    for package in revise_packages:
        for check in package["llm_secondary_feedback"]["revise_or_reject_checks"]:
            check_counter[check["check_id"]][check["verdict"]] += 1

    counts = {
        "pass_queue": len(pass_queue),
        "revision_packages": len(revise_packages),
        "reject_worklist": len(reject_worklist),
        "revision_attempted": sum(1 for row in revision_results if row.get("status") == "completed"),
        "revision_prepared": sum(1 for row in revision_results if row.get("status") == "prepared"),
        "revision_errors": sum(1 for row in revision_results if row.get("status") == "error"),
        "revision_harness_passed": sum(1 for row in revision_results if row.get("harness_overall_pass") is True),
        "revision_harness_failed": sum(1 for row in revision_results if row.get("harness_overall_pass") is False),
        "revise_feedback_check_counts": {key: dict(value) for key, value in sorted(check_counter.items())},
    }
    report = {
        "version": "2026-06-24",
        "created_at": now_iso(),
        "mode": "codex_cli" if args.run_codex else "prepare_only",
        "inputs": {
            "llm_run_dir": str(args.llm_run_dir),
            "db": str(args.db),
            "output_schema": str(args.output_schema),
        },
        "outputs": {
            "pass_expert_review_queue": str(pass_queue_path),
            "revision_request_packages": str(revise_packages_path),
            "reject_reselection_worklist": str(reject_worklist_path),
            "revision_results": str(revision_results_path),
            "revision_harness_passed_index": str(revision_passed_path),
            "revision_harness_failed_index": str(revision_failed_path),
            "revision_error_index": str(revision_error_path),
            "json_report": str(report_json_path),
            "markdown_report": str(report_md_path),
        },
        "counts": counts,
        "policy": {
            "final_question_bank_storage": False,
            "reject_items_regenerated": False,
            "rag_use": "evidence_validation_only",
        },
    }
    write_json(report_json_path, report)
    write_text(report_md_path, build_markdown_report(report))
    print(json.dumps({"output_dir": str(output_dir), "counts": counts}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
