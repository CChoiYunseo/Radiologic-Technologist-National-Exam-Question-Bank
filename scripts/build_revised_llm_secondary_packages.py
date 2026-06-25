#!/usr/bin/env python3
"""Build LLM second-pass validation packages for revised draft items.

Input items are revised drafts that already passed the deterministic Harness.
The generated packages are for review only and do not approve or store final
question-bank records.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FOLLOWUP_DIR = (
    PROJECT_ROOT
    / "resources/generated/review_candidates/llm_secondary_validation_runs/run_20260624T074838Z_limitall_offset0/verdict_followup"
)
DEFAULT_OUTPUT_DIR = DEFAULT_FOLLOWUP_DIR / "revised_llm_secondary_validation"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def short_hash(value: str, length: int = 16) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


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


def output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["overall_verdict", "checks", "revision_required", "notes"],
        "properties": {
            "overall_verdict": {"enum": ["pass", "revise", "reject"]},
            "revision_required": {"type": "boolean"},
            "checks": {
                "type": "object",
                "required": [
                    "scope_alignment",
                    "learning_objective_alignment",
                    "evidence_grounding",
                    "answer_uniqueness",
                    "distractor_quality",
                    "explanation_quality",
                    "copyright_risk",
                    "korean_item_style",
                    "hold_material_contamination",
                ],
                "additionalProperties": {
                    "type": "object",
                    "required": ["verdict", "reason"],
                    "properties": {
                        "verdict": {"enum": ["pass", "revise", "reject"]},
                        "reason": {"type": "string"},
                    },
                },
            },
            "notes": {"type": "string"},
        },
    }


def draft_item_for_review(item: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "period",
        "subject",
        "field",
        "area",
        "detail",
        "scope_id",
        "learning_objective_id",
        "question_type",
        "competency_type",
        "difficulty",
        "stem",
        "options",
        "answer",
        "explanation",
        "distractor_strategy",
    ]
    return {key: item.get(key) for key in keys}


def scope_from_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "period": item.get("period"),
        "subject": item.get("subject"),
        "field": item.get("field"),
        "area": item.get("area"),
        "detail": item.get("detail"),
        "scope_id": item.get("scope_id"),
    }


def build_markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# 재작성 Harness 통과본 LLM 2차 검증 패키지 보고서",
        "",
        f"- 생성 시각: {report['created_at']}",
        f"- 입력 재작성 Harness 통과 수: {report['counts']['input_revision_harness_passed']}",
        f"- 생성한 LLM 검증 패키지 수: {report['counts']['llm_secondary_validation_packages']}",
        "",
        "## 산출물",
        f"- 패키지 JSONL: `{report['outputs']['llm_secondary_validation_packages']}`",
        f"- 재작성 후보 인덱스: `{report['outputs']['review_candidate_index']}`",
        f"- JSON 보고서: `{report['outputs']['json_report']}`",
        "",
        "## 주의",
        "- 이 패키지는 재작성본 재검증용이며 최종 승인본이 아닙니다.",
        "- LLM 2차 검증 실행은 별도 단계입니다.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--followup-dir", type=Path, default=DEFAULT_FOLLOWUP_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    revision_passed_path = args.followup_dir / "revision_harness_passed_index.jsonl"
    rows = read_jsonl(revision_passed_path)

    packages: list[dict[str, Any]] = []
    review_index: list[dict[str, Any]] = []
    for row in rows:
        revised_item_path = Path(row["revised_draft_item"])
        revision_package_path = revised_item_path.parent / "revision_package_snapshot.json"
        harness_report_path = Path(row["revision_harness_report"])
        revised_item = read_json(revised_item_path)
        revision_package = read_json(revision_package_path)
        harness_report = read_json(harness_report_path)

        review_candidate_id = f"rrc_{short_hash(row['revision_package_id'] + '|' + str(revised_item_path))}"
        validation_package_id = f"rllm2_{short_hash(review_candidate_id)}"
        package = {
            "validation_package_id": validation_package_id,
            "review_candidate_id": review_candidate_id,
            "package_id": row["package_id"],
            "revision_package_id": row["revision_package_id"],
            "source_review_candidate_id": row.get("review_candidate_id"),
            "mode": "revised_draft_llm_secondary_validation_input",
            "instructions": {
                "role": "방사선사 국가고시 1·2교시 텍스트 문항 재작성본 2차 검증자",
                "task": "재작성본이 이전 LLM 지적사항을 해소했고 출제범위, 학습목표, 근거, 문항작성 원칙에 맞는지 판정한다.",
                "do_not_approve_final_question_bank_storage": True,
                "rag_use": "정답 근거 검증용으로만 사용",
            },
            "draft_item": draft_item_for_review(revised_item),
            "requested_scope": revision_package.get("requested_scope") or scope_from_item(revised_item),
            "recommended_generation_settings": revision_package.get("recommended_generation_settings") or {},
            "previous_llm_secondary_feedback": revision_package.get("llm_secondary_feedback") or {},
            "harness_summary": harness_report.get("summary") or {},
            "harness_findings": harness_report.get("findings") or [],
            "evidence_for_review": revision_package.get("evidence_for_review") or [],
            "checks_requested": [
                "scope_alignment",
                "learning_objective_alignment",
                "evidence_grounding",
                "answer_uniqueness",
                "distractor_quality",
                "explanation_quality",
                "copyright_risk",
                "korean_item_style",
                "hold_material_contamination",
            ],
            "expected_output_schema": output_schema(),
            "source_paths": {
                "revised_draft_item": str(revised_item_path),
                "revision_package_snapshot": str(revision_package_path),
                "revision_harness_report": str(harness_report_path),
            },
        }
        packages.append(package)
        review_index.append(
            {
                "review_candidate_id": review_candidate_id,
                "source_review_candidate_id": row.get("review_candidate_id"),
                "validation_package_id": validation_package_id,
                "package_id": row["package_id"],
                "revision_package_id": row["revision_package_id"],
                "status": "revision_harness_passed_pending_llm_review",
                "scope": scope_from_item(revised_item),
                "learning_objective_id": revised_item.get("learning_objective_id"),
                "question_type": revised_item.get("question_type"),
                "difficulty": revised_item.get("difficulty"),
                "answer": revised_item.get("answer"),
                "option_count": len(revised_item.get("options") or []),
                "evidence_count": len(revised_item.get("evidence_refs") or []),
                "revised_draft_item_path": str(revised_item_path),
                "revision_harness_report_path": str(harness_report_path),
                "llm_secondary_validation_status": "not_run",
            }
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    packages_path = args.output_dir / "revised_llm_secondary_validation_packages.jsonl"
    index_path = args.output_dir / "revised_review_candidate_index.jsonl"
    report_json_path = args.output_dir / "revised_llm_secondary_package_build_report.json"
    report_md_path = args.output_dir / "revised_llm_secondary_package_build_report.md"
    write_jsonl(packages_path, packages)
    write_jsonl(index_path, review_index)
    report = {
        "version": "2026-06-24",
        "created_at": now_iso(),
        "inputs": {
            "followup_dir": str(args.followup_dir),
            "revision_harness_passed_index": str(revision_passed_path),
        },
        "outputs": {
            "llm_secondary_validation_packages": str(packages_path),
            "review_candidate_index": str(index_path),
            "json_report": str(report_json_path),
            "markdown_report": str(report_md_path),
        },
        "counts": {
            "input_revision_harness_passed": len(rows),
            "llm_secondary_validation_packages": len(packages),
            "review_candidate_index": len(review_index),
        },
        "policy": {
            "final_question_bank_storage": False,
            "llm_secondary_validation_executed": False,
            "rag_use": "evidence_validation_only",
        },
    }
    write_json(report_json_path, report)
    report_md_path.write_text(build_markdown_report(report), encoding="utf-8")
    print(json.dumps(report["counts"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
