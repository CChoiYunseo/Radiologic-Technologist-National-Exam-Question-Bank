#!/usr/bin/env python3
"""Run LLM second-pass validation for generated question draft packages.

The script stores review verdicts only. It does not approve drafts and does
not write to the final question bank.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKAGES = (
    PROJECT_ROOT
    / "resources/generated/review_candidates/llm_secondary_validation_packages.jsonl"
)
DEFAULT_REVIEW_INDEX = PROJECT_ROOT / "resources/generated/review_candidates/review_candidate_index.jsonl"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources/generated/review_candidates/llm_secondary_validation_runs"
DEFAULT_SCHEMA = PROJECT_ROOT / "resources/rules/llm_secondary_validation_output_schema.json"

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
VALID_VERDICTS = {"pass", "revise", "reject"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


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


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


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
    return slug[:100] or "llm_secondary_validation"


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


def build_prompt(package: dict[str, Any]) -> str:
    compact = {
        "validation_package_id": package.get("validation_package_id"),
        "review_candidate_id": package.get("review_candidate_id"),
        "package_id": package.get("package_id"),
        "draft_item": package.get("draft_item"),
        "requested_scope": package.get("requested_scope"),
        "recommended_generation_settings": package.get("recommended_generation_settings"),
        "harness_summary": package.get("harness_summary"),
        "harness_findings": package.get("harness_findings"),
        "evidence_for_review": package.get("evidence_for_review"),
        "checks_requested": REQUIRED_CHECKS,
    }
    rules = [
        "JSON object만 출력한다. Markdown 코드블록을 쓰지 않는다.",
        "overall_verdict는 pass, revise, reject 중 하나만 사용한다.",
        "근거 발췌문은 정답 근거 검증용으로만 사용한다.",
        "전공서 원문과 문항/보기/해설의 과도한 유사성이 보이면 copyright_risk를 revise 또는 reject로 표시한다.",
        "표·그림·수식·법규·수치 보류 자료가 문항 근거로 쓰인 흔적이 있으면 hold_material_contamination을 revise 또는 reject로 표시한다.",
        "정답이 둘 이상 가능하거나 정답 근거가 부족하면 answer_uniqueness 또는 evidence_grounding을 revise/reject로 표시한다.",
        "국가고시 문항으로 문장, 보기 균질성, 해설 품질이 부족하면 korean_item_style 또는 explanation_quality를 revise로 표시한다.",
        "최종 문제은행 승인 여부는 판단하지 않는다. 이 판정은 2차 검증 결과일 뿐이다.",
    ]
    return (
        "너는 방사선사 국가고시 1·2교시 텍스트 문항의 2차 검증자다.\n"
        "아래 규칙을 따른다.\n"
        + "\n".join(f"- {rule}" for rule in rules)
        + "\n\n각 checks 항목은 반드시 verdict와 reason을 가진다.\n"
        + "필수 checks: "
        + ", ".join(REQUIRED_CHECKS)
        + "\n\n검증 입력 JSON:\n"
        + json.dumps(compact, ensure_ascii=False, indent=2)
        + "\n\n출력 JSON 형식:\n"
        + json.dumps(package.get("expected_output_schema"), ensure_ascii=False, indent=2)
        + "\n\n검증 결과 JSON object만 출력하라.\n"
    )


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


def normalize_result(result: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    verdict = result.get("overall_verdict")
    if verdict not in VALID_VERDICTS:
        errors.append(f"invalid overall_verdict: {verdict}")
        result["overall_verdict"] = "revise"
    result["revision_required"] = bool(result.get("revision_required"))
    checks = result.get("checks")
    if not isinstance(checks, dict):
        checks = {}
        result["checks"] = checks
        errors.append("checks is missing or not an object")
    for check_id in REQUIRED_CHECKS:
        check = checks.get(check_id)
        if not isinstance(check, dict):
            checks[check_id] = {
                "verdict": "revise",
                "reason": "LLM 검증 결과에 필수 검사 항목이 누락되었습니다.",
            }
            errors.append(f"missing check: {check_id}")
            continue
        if check.get("verdict") not in VALID_VERDICTS:
            check["verdict"] = "revise"
            errors.append(f"invalid check verdict: {check_id}")
        if not isinstance(check.get("reason"), str) or not check.get("reason", "").strip():
            check["reason"] = "검증 사유가 비어 있어 재검토가 필요합니다."
            errors.append(f"missing check reason: {check_id}")
    if not isinstance(result.get("notes"), str):
        result["notes"] = ""
        errors.append("notes is missing or not a string")
    if result["overall_verdict"] in {"revise", "reject"}:
        result["revision_required"] = True
    return result, errors


def result_row(
    package: dict[str, Any],
    result: dict[str, Any] | None,
    status: str,
    run_dir: Path,
    error: str = "",
    normalization_errors: list[str] | None = None,
) -> dict[str, Any]:
    draft = package.get("draft_item") or {}
    scope = package.get("requested_scope") or {}
    return {
        "validation_package_id": package.get("validation_package_id"),
        "review_candidate_id": package.get("review_candidate_id"),
        "package_id": package.get("package_id"),
        "status": status,
        "overall_verdict": (result or {}).get("overall_verdict"),
        "revision_required": (result or {}).get("revision_required"),
        "normalization_errors": normalization_errors or [],
        "error": error,
        "period": draft.get("period") or scope.get("period"),
        "subject": draft.get("subject") or scope.get("subject"),
        "field": draft.get("field") or scope.get("field"),
        "area": draft.get("area") or scope.get("area"),
        "detail": draft.get("detail") or scope.get("detail"),
        "scope_id": draft.get("scope_id") or scope.get("scope_id"),
        "learning_objective_id": draft.get("learning_objective_id"),
        "question_type": draft.get("question_type"),
        "difficulty": draft.get("difficulty"),
        "run_dir": str(run_dir),
        "llm_validation_result": str(run_dir / "llm_validation_result.json"),
        "validation_run_report": str(run_dir / "validation_run_report.json"),
    }


def verdict_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"pass": 0, "revise": 0, "reject": 0, "error": 0}
    for row in rows:
        if row.get("status") == "error":
            counts["error"] += 1
        elif row.get("overall_verdict") in counts:
            counts[str(row.get("overall_verdict"))] += 1
    return counts


def build_markdown_report(report: dict[str, Any]) -> str:
    counts = report["counts"]
    lines = [
        "# LLM 2차 검증 Batch 보고서",
        "",
        f"- 실행 시각: {report['created_at']}",
        f"- 실행 모드: {report['mode']}",
        f"- 대상 패키지 수: {counts['selected_packages']}",
        f"- 완료: {counts['completed']}",
        f"- 오류: {counts['errors']}",
        f"- pass: {counts['verdicts']['pass']}",
        f"- revise: {counts['verdicts']['revise']}",
        f"- reject: {counts['verdicts']['reject']}",
        "",
        "## 산출물",
        f"- 결과 JSONL: `{report['outputs']['results_index']}`",
        f"- 통합 후보+판정 JSONL: `{report['outputs']['review_candidate_index_with_verdicts']}`",
        f"- JSON 보고서: `{report['outputs']['json_report']}`",
        "",
        "## 주의",
        "- 이 결과는 LLM 2차 검증 판정이며 최종 승인 상태가 아닙니다.",
        "- revise/reject 항목은 재작성 또는 근거 재검색 후 다시 Harness와 LLM 검증을 거쳐야 합니다.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages", type=Path, default=DEFAULT_PACKAGES)
    parser.add_argument("--review-index", type=Path, default=DEFAULT_REVIEW_INDEX)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--limit", type=int, default=0, help="0 means all packages.")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--resume-run-dir", type=Path, default=None)
    parser.add_argument("--run-codex", action="store_true")
    args = parser.parse_args()

    packages = read_jsonl(args.packages)
    selected = packages[args.offset :]
    if args.limit > 0:
        selected = selected[: args.limit]

    if args.resume_run_dir:
        batch_dir = args.resume_run_dir
        batch_dir.mkdir(parents=True, exist_ok=True)
        mode = "codex_cli_resume" if args.run_codex else "prepare_only_resume"
    else:
        batch_dir = args.output_dir / f"run_{run_id()}_limit{args.limit or 'all'}_offset{args.offset}"
        batch_dir.mkdir(parents=True, exist_ok=False)
        mode = "codex_cli" if args.run_codex else "prepare_only"

    rows: list[dict[str, Any]] = []
    for package in selected:
        validation_package_id = str(package["validation_package_id"])
        item_dir = batch_dir / safe_slug(validation_package_id)
        prompt_path = item_dir / "prompt.txt"
        package_path = item_dir / "validation_package_snapshot.json"
        raw_path = item_dir / "codex_last_message.json"
        result_path = item_dir / "llm_validation_result.json"
        item_report_path = item_dir / "validation_run_report.json"
        prompt = build_prompt(package)
        write_text(prompt_path, prompt)
        write_json(package_path, package)

        if result_path.exists():
            result = json.loads(result_path.read_text(encoding="utf-8"))
            normalized, normalization_errors = normalize_result(result)
            write_json(result_path, normalized)
            row = result_row(package, normalized, "completed", item_dir, normalization_errors=normalization_errors)
            rows.append(row)
            continue

        if not args.run_codex:
            row = result_row(package, None, "prepared", item_dir)
            write_json(
                item_report_path,
                {
                    "created_at": now_iso(),
                    "mode": "prepare_only",
                    "package_id": package.get("package_id"),
                    "validation_package_id": validation_package_id,
                    "outputs": {
                        "prompt": str(prompt_path),
                        "package_snapshot": str(package_path),
                    },
                },
            )
            rows.append(row)
            continue

        try:
            result = run_codex(prompt, args.schema, raw_path, args.model, args.timeout)
            normalized, normalization_errors = normalize_result(result)
            write_json(result_path, normalized)
            row = result_row(package, normalized, "completed", item_dir, normalization_errors=normalization_errors)
            write_json(
                item_report_path,
                {
                    "created_at": now_iso(),
                    "mode": "codex_cli",
                    "package_id": package.get("package_id"),
                    "validation_package_id": validation_package_id,
                    "outputs": {
                        "prompt": str(prompt_path),
                        "package_snapshot": str(package_path),
                        "raw_codex_last_message": str(raw_path),
                        "llm_validation_result": str(result_path),
                    },
                    "normalization_errors": normalization_errors,
                    "result_summary": {
                        "overall_verdict": normalized.get("overall_verdict"),
                        "revision_required": normalized.get("revision_required"),
                    },
                },
            )
        except Exception as exc:
            row = result_row(package, None, "error", item_dir, error=str(exc)[-3000:])
            write_json(
                item_report_path,
                {
                    "created_at": now_iso(),
                    "mode": "error",
                    "package_id": package.get("package_id"),
                    "validation_package_id": validation_package_id,
                    "error": row["error"],
                    "outputs": {
                        "prompt": str(prompt_path),
                        "package_snapshot": str(package_path),
                    },
                },
            )
        rows.append(row)

    by_candidate = {row["review_candidate_id"]: row for row in rows}
    review_index_rows = read_jsonl(args.review_index)
    enriched_review_rows: list[dict[str, Any]] = []
    for candidate in review_index_rows:
        enriched = dict(candidate)
        verdict = by_candidate.get(candidate.get("review_candidate_id"))
        if verdict:
            enriched["llm_secondary_validation_status"] = verdict["status"]
            enriched["llm_secondary_validation_verdict"] = verdict.get("overall_verdict")
            enriched["llm_secondary_revision_required"] = verdict.get("revision_required")
            enriched["llm_secondary_validation_result_path"] = verdict.get("llm_validation_result")
            enriched["llm_secondary_validation_run_dir"] = verdict.get("run_dir")
        enriched_review_rows.append(enriched)

    results_index = batch_dir / "llm_secondary_validation_results.jsonl"
    enriched_index = batch_dir / "review_candidate_index_with_llm_verdicts.jsonl"
    pass_index = batch_dir / "llm_secondary_pass_index.jsonl"
    revise_index = batch_dir / "llm_secondary_revise_index.jsonl"
    reject_index = batch_dir / "llm_secondary_reject_index.jsonl"
    error_index = batch_dir / "llm_secondary_error_index.jsonl"
    report_json = batch_dir / "llm_secondary_validation_batch_report.json"
    report_md = batch_dir / "llm_secondary_validation_batch_report.md"

    write_jsonl(results_index, rows)
    write_jsonl(enriched_index, enriched_review_rows)
    write_jsonl(pass_index, [row for row in rows if row.get("overall_verdict") == "pass"])
    write_jsonl(revise_index, [row for row in rows if row.get("overall_verdict") == "revise"])
    write_jsonl(reject_index, [row for row in rows if row.get("overall_verdict") == "reject"])
    write_jsonl(error_index, [row for row in rows if row.get("status") == "error"])

    counts = {
        "input_packages": len(packages),
        "selected_packages": len(selected),
        "completed": sum(1 for row in rows if row.get("status") == "completed"),
        "prepared": sum(1 for row in rows if row.get("status") == "prepared"),
        "errors": sum(1 for row in rows if row.get("status") == "error"),
        "verdicts": verdict_counts(rows),
    }
    report = {
        "version": "2026-06-24",
        "created_at": now_iso(),
        "mode": mode,
        "inputs": {
            "packages": str(args.packages),
            "review_candidate_index": str(args.review_index),
            "schema": str(args.schema),
        },
        "outputs": {
            "batch_dir": str(batch_dir),
            "results_index": str(results_index),
            "review_candidate_index_with_verdicts": str(enriched_index),
            "pass_index": str(pass_index),
            "revise_index": str(revise_index),
            "reject_index": str(reject_index),
            "error_index": str(error_index),
            "json_report": str(report_json),
            "markdown_report": str(report_md),
        },
        "counts": counts,
        "policy": {
            "final_question_bank_storage": False,
            "rag_use": "evidence_validation_only",
            "llm_secondary_validation_executed": bool(args.run_codex),
        },
    }
    write_json(report_json, report)
    write_text(report_md, build_markdown_report(report))
    print(json.dumps({"batch_dir": str(batch_dir), "counts": counts}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
