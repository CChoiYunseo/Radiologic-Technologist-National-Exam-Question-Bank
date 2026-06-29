#!/usr/bin/env python3
"""Generate visual-question drafts from approved visual request packages.

This is disabled by default because the current 1·2교시 exam pipeline is
text-only. Use the explicit research flag only for non-exam experiments.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from question_option_randomizer import reorder_item_answer_position

DEFAULT_PACKAGES = (
    PROJECT_ROOT
    / "resources"
    / "generated"
    / "visual_question_request_packages"
    / "visual_question_request_packages_ready.jsonl"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources" / "generated" / "visual_question_drafts"
OUTPUT_SCHEMA = PROJECT_ROOT / "resources" / "rules" / "generated_question_output_schema.json"
QUESTION_TYPES = {"개념형", "비교형", "계산형", "법규형", "검사절차형", "안전관리형", "영상해석형"}
DIFFICULTIES = {"하", "중", "상"}
HOLD_TEXT = re.compile(r"(제\s*\d+\s*조|시행규칙|시행령|고시|별표|NEMA|IEC|KS|허용한도|법령)")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def batch_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
    return slug[:90] or "visual_question"


def scope_label(package: dict[str, Any]) -> str:
    scope = package.get("requested_scope") or {}
    return " / ".join(scope.get(key, "") for key in ["period", "subject", "field", "area", "detail"] if scope.get(key))


def compact_text(value: Any, max_chars: int = 1600) -> str:
    text = " ".join(str(value or "").split())
    return text[:max_chars]


def prompt_for(package: dict[str, Any], max_summary_chars: int) -> str:
    scope = package.get("requested_scope") or {}
    settings = package.get("recommended_generation_settings") or {}
    visual = package.get("visual_evidence") or {}
    summary = visual.get("summary") or {}
    evidence_payload = {
        "visual_approval_id": package.get("source_visual_approval_id"),
        "visual_kind": visual.get("visual_kind"),
        "source_file": visual.get("source_file"),
        "page_or_slide": visual.get("page_or_slide"),
        "allowed_question_modes": visual.get("allowed_question_modes") or [],
        "caption": compact_text(summary.get("caption"), 240),
        "nearby_text_summary": compact_text(summary.get("nearby_text_summary"), max_summary_chars),
        "semantic_description": compact_text(summary.get("semantic_description"), max_summary_chars),
        "structure_summary": compact_text(summary.get("structure_summary"), max_summary_chars),
        "formula_plain_text": compact_text(summary.get("formula_plain_text"), 400),
        "variables": summary.get("variables") or {},
        "table_json": summary.get("table_json") or [],
        "embedded_text_candidates": summary.get("embedded_text_candidates") or [],
    }
    fixed = {
        "period": scope.get("period", ""),
        "subject": scope.get("subject", ""),
        "field": scope.get("field", ""),
        "area": scope.get("area", ""),
        "detail": scope.get("detail", ""),
        "scope_id": scope.get("scope_id", ""),
        "learning_objective_id": settings.get("learning_objective_id", ""),
        "question_type": settings.get("question_type", "영상해석형"),
        "competency_type": settings.get("competency_type", "해석형"),
        "difficulty": settings.get("difficulty", "중"),
        "evidence_refs": [{"rag_input_id": package.get("source_visual_approval_id", "")}],
        "source_chunks": [{"rag_input_id": package.get("source_visual_approval_id", "")}],
        "llm_first_check": {
            "overall_verdict": "pass",
            "checks": {
                "scope_alignment": {"verdict": "pass", "reason": "생성 문항 기준으로 점검 이유 작성"},
                "learning_objective_alignment": {"verdict": "pass", "reason": "생성 문항 기준으로 점검 이유 작성"},
                "evidence_grounding": {"verdict": "pass", "reason": "생성 문항 기준으로 점검 이유 작성"},
                "answer_uniqueness": {"verdict": "pass", "reason": "생성 문항 기준으로 점검 이유 작성"},
                "option_quality": {"verdict": "pass", "reason": "생성 문항 기준으로 점검 이유 작성"},
                "explanation_quality": {"verdict": "pass", "reason": "생성 문항 기준으로 점검 이유 작성"},
                "copyright_safety": {"verdict": "pass", "reason": "생성 문항 기준으로 점검 이유 작성"},
                "text_only_policy": {"verdict": "pass", "reason": "비시험 연구용 시각자료 문항으로 1·2교시 텍스트 시험지에 포함하지 않음"},
            },
            "notes": "비시험 연구용 시각자료 문항의 Harness 전 LLM 1차 자기검토 요약",
        },
        "validation_status": "draft",
        "reviewer_agent_results": {
            "scope": "pending",
            "grounding": "pending",
            "uniqueness": "pending",
            "grammar": "pending",
            "copyright": "pending",
        },
        "final_judge": "pending_visual_harness",
        "status": "generated",
    }
    payload = {
        "task": "방사선사 국가고시 1·2교시 시각자료 기반 5지선다 문제 초안 생성",
        "scope": scope,
        "visual_evidence": evidence_payload,
        "fixed_output_fields": fixed,
        "output_schema": {
            "stem": "구조화 설명을 근거로 새 문장으로 작성한 질문",
            "options": "정답 1개와 오답 4개, 총 5개",
            "answer": "1~5 정수",
            "explanation": "구조화 설명에 근거한 간결한 새 문장 해설",
            "distractor_strategy": "오답 구성 원칙",
        },
    }
    rules = [
        "JSON object만 출력한다. Markdown 코드블록을 쓰지 않는다.",
        "fixed_output_fields의 메타데이터 값은 그대로 사용한다.",
        "원본 표·그림·수식 이미지를 문제에 직접 넣거나 재현하지 않는다.",
        "caption, embedded_text_candidates, formula_plain_text, table_json 문구를 그대로 베껴 쓰지 않는다.",
        "문제, 보기, 해설은 모두 새 문장으로 작성한다.",
        "법규, 조문, 최신 기준, 허용 기준, 새로운 수치 기준을 추가하지 않는다.",
        "구조화 설명에 없는 내용을 추정해서 정답 근거로 사용하지 않는다.",
        "정답은 반드시 하나만 되도록 한다.",
        "계산형이라도 복잡한 계산값을 새로 요구하지 말고 공식의 의미나 변수 관계를 묻는다.",
        "실제 영상 판독 문제처럼 보이게 만들지 말고, 구조·관계·흐름·의미 해석 문항으로 만든다.",
        "llm_first_check를 작성하고 모든 check를 pass로 만들 수 있을 때만 최종 JSON을 출력한다.",
        "이 문항은 비시험 연구용이며 1·2교시 실전 텍스트 시험지에는 포함하지 않는다.",
    ]
    return (
        "너는 방사선사 국가고시 1·2교시 문항 초안 생성기다.\n"
        "아래 규칙을 반드시 따른다.\n"
        + "\n".join(f"- {rule}" for rule in rules)
        + "\n\n입력 JSON:\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n\n위 입력에 대한 JSON object만 출력하라.\n"
    )


def extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def run_codex(prompt: str, raw_output: Path, model: str, timeout: int) -> dict[str, Any]:
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
        str(OUTPUT_SCHEMA),
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


def validate_visual_item(item: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    scope = package.get("requested_scope") or {}
    required = [
        "period",
        "subject",
        "field",
        "area",
        "detail",
        "scope_id",
        "question_type",
        "competency_type",
        "difficulty",
        "stem",
        "options",
        "answer",
        "explanation",
        "evidence_refs",
        "source_chunks",
        "llm_first_check",
        "distractor_strategy",
        "validation_status",
        "reviewer_agent_results",
        "final_judge",
        "status",
    ]
    missing = [field for field in required if item.get(field) in (None, "", [])]
    if missing:
        findings.append({"check_id": "VV-001", "severity": "error", "status": "fail", "message": "필수 필드가 부족합니다.", "details": {"missing": missing}})
    for key in ["period", "subject", "field", "area", "detail", "scope_id"]:
        if item.get(key) != scope.get(key):
            findings.append({"check_id": "VV-002", "severity": "error", "status": "fail", "message": "출제범위 메타데이터가 패키지와 다릅니다.", "details": {"field": key}})
    options = item.get("options") or []
    if not isinstance(options, list) or len(options) != 5:
        findings.append({"check_id": "VV-003", "severity": "error", "status": "fail", "message": "보기는 5개여야 합니다.", "details": {"option_count": len(options) if isinstance(options, list) else None}})
    if item.get("answer") not in {1, 2, 3, 4, 5}:
        findings.append({"check_id": "VV-004", "severity": "error", "status": "fail", "message": "정답은 1~5 정수여야 합니다.", "details": {"answer": item.get("answer")}})
    llm_first = item.get("llm_first_check") or {}
    if not isinstance(llm_first, dict) or llm_first.get("overall_verdict") != "pass":
        findings.append({"check_id": "VV-011", "severity": "error", "status": "fail", "message": "LLM 1차 검증 결과가 없거나 pass가 아닙니다.", "details": {"overall_verdict": llm_first.get("overall_verdict") if isinstance(llm_first, dict) else None}})
    if item.get("difficulty") not in DIFFICULTIES:
        findings.append({"check_id": "VV-005", "severity": "error", "status": "fail", "message": "난이도 값이 허용 범위를 벗어났습니다.", "details": {"difficulty": item.get("difficulty")}})
    if item.get("question_type") not in QUESTION_TYPES:
        findings.append({"check_id": "VV-006", "severity": "warning", "status": "warn", "message": "등록되지 않은 문항 유형입니다.", "details": {"question_type": item.get("question_type")}})
    evidence_id = package.get("source_visual_approval_id")
    refs = item.get("evidence_refs") or []
    if not refs or refs[0].get("rag_input_id") != evidence_id:
        findings.append({"check_id": "VV-007", "severity": "error", "status": "fail", "message": "시각자료 승인 ID 근거가 유지되지 않았습니다.", "details": {"expected": evidence_id}})
    generated = "\n".join([str(item.get("stem", "")), str(item.get("explanation", "")), *[str(opt) for opt in options]])
    if HOLD_TEXT.search(generated):
        findings.append({"check_id": "VV-008", "severity": "error", "status": "fail", "message": "법규·기준·표준 관련 보류 표현이 생성문에 포함됐습니다.", "details": {}})
    duplicate_options = [option for option, count in Counter(str(opt).strip() for opt in options).items() if option and count > 1]
    if duplicate_options:
        findings.append({"check_id": "VV-009", "severity": "error", "status": "fail", "message": "중복 보기가 있습니다.", "details": {"duplicates": duplicate_options}})
    if not findings:
        findings.append({"check_id": "VV-010", "severity": "info", "status": "pass", "message": "시각자료 전용 1차 검증을 통과했습니다.", "details": {}})
    errors = sum(1 for finding in findings if finding["severity"] == "error" and finding["status"] != "pass")
    warnings = sum(1 for finding in findings if finding["severity"] == "warning" and finding["status"] != "pass")
    return {
        "version": "2026-06-25",
        "created_at": now_iso(),
        "mode": "visual_generated_item",
        "summary": {
            "overall_pass": errors == 0,
            "error_count": errors,
            "warning_count": warnings,
            "info_count": sum(1 for finding in findings if finding["severity"] == "info"),
            "finding_count": len(findings),
        },
        "findings": findings,
        "policy": {
            "llm_used": True,
            "generation_approval_granted": False,
            "final_expert_approval_required": True,
            "source_visual_reuse_allowed": False,
        },
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# 시각자료 문항 생성 Batch 보고서",
        "",
        f"- 생성 시각: {report['created_at']}",
        f"- 실행 모드: {report['mode']}",
        f"- 입력 패키지: `{report['inputs']['packages']}`",
        f"- 대상 패키지 수: {report['counts']['selected_packages']}",
        f"- 생성 시도: {report['counts']['attempted']}",
        f"- 통과: {report['counts']['passed']}",
        f"- 실패: {report['counts']['failed']}",
        f"- 오류: {report['counts']['errors']}",
        f"- 준비만 수행: {report['counts']['prepare_only']}",
        "",
        "## 결과",
    ]
    for item in report["results"]:
        lines.append(f"- {item['status']}: `{item['package_id']}` | {item['scope_label']} | `{item['run_dir']}`")
    lines.extend(
        [
            "",
            "## 주의",
            "- 이 결과는 시각자료 기반 문항 초안입니다.",
            "- 전문가 검수 전에는 최종 문제은행 승인으로 전환하지 않습니다.",
            "- 원본 이미지·표·수식 자체를 재사용하지 않고 구조화 설명만 사용했습니다.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages", type=Path, default=DEFAULT_PACKAGES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--max-summary-chars", type=int, default=1400)
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--run-codex", action="store_true")
    parser.add_argument(
        "--allow-nonexam-visual-research",
        action="store_true",
        help="Run visual-question experiments outside the 1·2교시 text-only exam pipeline.",
    )
    args = parser.parse_args()

    if not args.allow_nonexam_visual_research:
        raise SystemExit(
            "시각자료 기반 문항 생성은 현재 1·2교시 실전 시험지 파이프라인에서 사용하지 않습니다. "
            "비시험 연구용 실험이 필요한 경우에만 --allow-nonexam-visual-research를 명시하세요."
        )

    packages = [pkg for pkg in read_jsonl(args.packages) if pkg.get("package_status") == "ready_visual"]
    selected = packages[args.offset : args.offset + args.limit]
    bid = batch_id()
    batch_dir = args.output_dir / f"batch_{bid}_limit{args.limit}_offset{args.offset}"
    batch_dir.mkdir(parents=True, exist_ok=True)

    counts = {
        "input_packages": len(packages),
        "selected_packages": len(selected),
        "attempted": 0,
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "prepare_only": 0,
    }
    results: list[dict[str, Any]] = []

    for package in selected:
        counts["attempted"] += 1
        package_id = package.get("package_id", "visual_package")
        run_dir = batch_dir / safe_slug(package_id)
        prompt_path = run_dir / "prompt.txt"
        package_path = run_dir / "request_package_snapshot.json"
        raw_path = run_dir / "codex_last_message.json"
        draft_path = run_dir / "draft_item.json"
        validation_path = run_dir / "visual_validation_report.json"
        item_report_path = run_dir / "dry_run_report.json"
        result_row = {
            "package_id": package_id,
            "source_visual_approval_id": package.get("source_visual_approval_id"),
            "scope_label": scope_label(package),
            "run_dir": str(run_dir),
            "status": "prepare_only",
            "validation_summary": {},
            "error": "",
        }
        try:
            prompt = prompt_for(package, args.max_summary_chars)
            write_text(prompt_path, prompt)
            write_json(package_path, package)
            if not args.run_codex:
                counts["prepare_only"] += 1
            else:
                draft = run_codex(prompt, raw_path, args.model, args.timeout)
                draft, answer_position_info = reorder_item_answer_position(
                    draft,
                    seed_parts=[package_id, result_row["scope_label"], draft.get("stem", "")],
                )
                write_json(draft_path, draft)
                validation = validate_visual_item(draft, package)
                write_json(validation_path, validation)
                result_row["validation_summary"] = validation["summary"]
                if validation["summary"]["overall_pass"]:
                    result_row["status"] = "passed"
                    counts["passed"] += 1
                else:
                    result_row["status"] = "failed"
                    counts["failed"] += 1
            write_json(
                item_report_path,
                {
                    "created_at": now_iso(),
                    "mode": "codex_cli" if args.run_codex else "prepare_only",
                    "package_id": package_id,
                    "scope_label": result_row["scope_label"],
                    "outputs": {
                        "prompt": str(prompt_path),
                        "package_snapshot": str(package_path),
                        "raw_codex_last_message": str(raw_path),
                        "draft_item": str(draft_path),
                        "validation_report": str(validation_path),
                    },
                    "answer_position_randomization": answer_position_info if args.run_codex else {},
                    "validation_summary": result_row["validation_summary"],
                },
            )
        except Exception as exc:
            result_row["status"] = "error"
            result_row["error"] = str(exc)[-2000:]
            counts["errors"] += 1
            write_json(item_report_path, {"created_at": now_iso(), "mode": "error", "package_id": package_id, "error": result_row["error"]})
        results.append(result_row)

    passed = [row for row in results if row["status"] == "passed"]
    failed = [row for row in results if row["status"] == "failed"]
    errors = [row for row in results if row["status"] == "error"]
    prepared = [row for row in results if row["status"] == "prepare_only"]
    outputs = {
        "passed_drafts_index": str(batch_dir / "passed_visual_drafts_index.jsonl"),
        "failed_drafts_index": str(batch_dir / "failed_visual_drafts_index.jsonl"),
        "error_drafts_index": str(batch_dir / "error_visual_drafts_index.jsonl"),
        "prepared_drafts_index": str(batch_dir / "prepared_visual_drafts_index.jsonl"),
    }
    write_jsonl(Path(outputs["passed_drafts_index"]), passed)
    write_jsonl(Path(outputs["failed_drafts_index"]), failed)
    write_jsonl(Path(outputs["error_drafts_index"]), errors)
    write_jsonl(Path(outputs["prepared_drafts_index"]), prepared)

    report = {
        "version": "2026-06-25",
        "created_at": now_iso(),
        "mode": "codex_cli" if args.run_codex else "prepare_only",
        "inputs": {"packages": str(args.packages), "output_schema": str(OUTPUT_SCHEMA)},
        "batch_dir": str(batch_dir),
        "counts": counts,
        "results": results,
        "outputs": outputs,
        "policy": {
            "final_question_bank_storage": False,
            "source_visual_reuse_allowed": False,
            "expert_review_required": True,
        },
    }
    report_json = batch_dir / "batch_report.json"
    report_md = batch_dir / "batch_report.md"
    write_json(report_json, report)
    report_md.write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps({"batch_dir": str(batch_dir), "counts": counts, "outputs": outputs}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
