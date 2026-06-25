#!/usr/bin/env python3
"""Prepare or run one local-Codex question generation dry run.

This script does not write to the final question bank. It creates one draft
item from a strict request package, then runs the deterministic harness.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from validate_rule_based_generation_harness import validate_payload


DEFAULT_PACKAGES = PROJECT_ROOT / "resources" / "generated" / "question_request_packages" / "question_request_packages_ready_strict.jsonl"
DEFAULT_DB = PROJECT_ROOT / "resources" / "extracted" / "rag_search_index_text_bm25" / "rag_text_bm25.sqlite"
DEFAULT_ANSWER_EVIDENCE_VECTOR_DB = PROJECT_ROOT / "resources" / "vector_db" / "subject_references"
DEFAULT_GENERATION_SAFE_VECTOR_DB = PROJECT_ROOT / "resources" / "vector_db" / "subject_references_generation_safe"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources" / "generated" / "question_drafts"
OUTPUT_SCHEMA = PROJECT_ROOT / "resources" / "rules" / "generated_question_output_schema.json"

DISALLOWED_DEFAULT_TYPES = {"법규형", "계산형", "영상해석형"}
DISALLOWED_EVIDENCE_MARKERS = ("법령", "조문", "표", "그림", "수식", "공식", "영상")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def load_vector_rag_ids(db_dir: Path) -> set[str]:
    db_path = db_dir / "chunks.sqlite"
    if not db_path.exists():
        raise FileNotFoundError(f"Vector DB chunks.sqlite not found: {db_path}")
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT chunk_id, metadata_json FROM chunks").fetchall()
    finally:
        conn.close()
    ids = set()
    for chunk_id, metadata_json in rows:
        rag_input_id = ""
        if metadata_json:
            try:
                metadata = json.loads(metadata_json)
                rag_input_id = metadata.get("rag_input_id") or ""
            except json.JSONDecodeError:
                rag_input_id = ""
        ids.add(str(rag_input_id or chunk_id))
    return {item for item in ids if item}


def package_safe_ref_errors(package: dict[str, Any], generation_safe_ids: set[str]) -> list[str]:
    errors = []
    for ref in package.get("evidence_refs") or []:
        rag_input_id = ref.get("rag_input_id") if isinstance(ref, dict) else str(ref)
        if rag_input_id not in generation_safe_ids:
            errors.append(rag_input_id)
    return sorted(set(errors))


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", value).strip("_")
    return slug[:80] or "question_dry_run"


def package_is_default_safe(package: dict[str, Any]) -> bool:
    settings = package.get("recommended_generation_settings") or {}
    qtypes = set(settings.get("question_type_candidates") or [])
    evidence_types = " ".join(settings.get("required_evidence_types") or [])
    if qtypes and qtypes.issubset(DISALLOWED_DEFAULT_TYPES):
        return False
    if qtypes & DISALLOWED_DEFAULT_TYPES and "개념형" not in qtypes and "비교형" not in qtypes:
        return False
    return not any(marker in evidence_types for marker in DISALLOWED_EVIDENCE_MARKERS)


def select_package(packages: list[dict[str, Any]], package_id: str, package_index: int) -> dict[str, Any]:
    if package_id:
        for package in packages:
            if package.get("package_id") == package_id:
                return package
        raise SystemExit(f"package_id not found: {package_id}")
    for package in packages:
        if package_is_default_safe(package):
            return package
    if not packages:
        raise SystemExit("No request packages found")
    return packages[package_index]


def db_chunk(conn: sqlite3.Connection, rag_input_id: str) -> dict[str, Any] | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM chunks WHERE rag_input_id = ?", (rag_input_id,)).fetchone()
    return dict(row) if row else None


def evidence_for_prompt(package: dict[str, Any], conn: sqlite3.Connection, max_chars: int) -> list[dict[str, Any]]:
    evidence = []
    for ref in package.get("evidence_refs") or []:
        rag_input_id = ref.get("rag_input_id")
        row = db_chunk(conn, rag_input_id)
        if not row:
            continue
        content = " ".join(str(row.get("content") or "").split())
        evidence.append(
            {
                "rag_input_id": rag_input_id,
                "source_file": row.get("source_file"),
                "page_or_slide": row.get("page_or_slide"),
                "scope_mapping_confidence": row.get("scope_mapping_confidence"),
                "content_excerpt": content[:max_chars],
            }
        )
    return evidence


def choose_question_type(package: dict[str, Any]) -> str:
    qtypes = (package.get("recommended_generation_settings") or {}).get("question_type_candidates") or []
    for preferred in ["개념형", "비교형", "검사절차형", "안전관리형"]:
        if preferred in qtypes:
            return preferred
    for qtype in qtypes:
        if qtype not in DISALLOWED_DEFAULT_TYPES:
            return qtype
    return "개념형"


def choose_difficulty(package: dict[str, Any]) -> str:
    difficulties = (package.get("recommended_generation_settings") or {}).get("difficulty_candidates") or []
    for preferred in ["중", "하", "상"]:
        if preferred in difficulties:
            return preferred
    return "중"


def choose_learning_objective(package: dict[str, Any]) -> dict[str, Any]:
    candidates = (package.get("recommended_generation_settings") or {}).get("learning_objective_candidates") or []
    return candidates[0] if candidates else {}


def build_prompt(package: dict[str, Any], evidence: list[dict[str, Any]]) -> str:
    scope = package.get("requested_scope") or {}
    learning_objective = choose_learning_objective(package)
    question_type = choose_question_type(package)
    difficulty = choose_difficulty(package)
    generation_variant = package.get("generation_variant") or {}
    source_chunks = [{"rag_input_id": item["rag_input_id"]} for item in evidence]
    payload = {
        "task": "방사선사 국가고시 1·2교시 텍스트 기반 5지선다 문제 dry-run 생성",
        "scope": scope,
        "learning_objective": learning_objective,
        "question_type": question_type,
        "difficulty": difficulty,
        "generation_variant": generation_variant,
        "rag_index_policy": package.get("rag_index_policy") or {
            "generation_policy": "automatic_generation_requires_generation_safe_index",
        },
        "evidence": evidence,
        "fixed_output_fields": {
            "period": scope.get("period", ""),
            "subject": scope.get("subject", ""),
            "field": scope.get("field", ""),
            "area": scope.get("area", ""),
            "detail": scope.get("detail", ""),
            "scope_id": scope.get("scope_id", ""),
            "learning_objective_id": learning_objective.get("learning_objective_id", ""),
            "question_type": question_type,
            "competency_type": "암기형" if question_type == "개념형" else "해석형",
            "difficulty": difficulty,
            "evidence_refs": source_chunks,
            "source_chunks": source_chunks,
            "validation_status": "draft",
            "reviewer_agent_results": {
                "scope": "pending",
                "grounding": "pending",
                "uniqueness": "pending",
                "grammar": "pending",
                "copyright": "pending"
            },
            "final_judge": "pending_rule_harness",
            "status": "generated"
        },
        "output_schema_summary": {
            "stem": "새 문장으로 작성한 질문 줄기",
            "options": "서로 중복되지 않는 보기 5개 배열",
            "answer": "정답 보기 번호 1~5 중 하나",
            "explanation": "근거 내용을 바탕으로 새 문장으로 작성한 간결한 해설",
            "distractor_strategy": "오답 구성 원칙을 한 문장으로 설명"
        },
    }
    rules = [
        "JSON object만 출력한다. Markdown 코드블록을 쓰지 않는다.",
        "전공서 원문 문장을 그대로 복사하거나 가깝게 재서술하지 않는다.",
        "문제, 보기, 해설은 모두 새 문장으로 작성한다.",
        "RAG 근거는 정답 판단 근거로만 사용한다.",
        "근거에 없는 지식, 법규 조문, 수치 기준, 공식, 표·그림 내용을 추가하지 않는다.",
        "정답은 반드시 하나만 되도록 하고, 보기는 모두 같은 문장 길이와 문체에 가깝게 맞춘다.",
        "generation_variant가 제공되면 그 초점에 맞춰 같은 범위의 다른 변형 문항을 만든다.",
        "문항은 실제 저장용이 아니라 dry-run 초안이므로 validation_status는 draft로 유지한다.",
    ]
    return (
        "너는 보건의료 국가시험 문항 초안을 만드는 생성기다.\n"
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


def run_codex(prompt: str, output_schema: Path, raw_output: Path, model: str, timeout: int) -> dict[str, Any]:
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
        str(output_schema),
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
    raw_text = raw_output.read_text(encoding="utf-8")
    return extract_json_object(raw_text)


def validate_item(item: dict[str, Any], conn: sqlite3.Connection, generation_safe_ids: set[str] | None = None) -> dict[str, Any]:
    payload = {"mode": "generated_item", "item": item}
    return validate_payload(payload, conn, generation_safe_ids)


def markdown_summary(report: dict[str, Any]) -> str:
    validation = report.get("validation_report") or {}
    summary = validation.get("summary") or {}
    lines = [
        "# 문제 생성 Dry-Run 보고서",
        "",
        f"- 생성 시각: {report['created_at']}",
        f"- 실행 모드: {report['mode']}",
        f"- 패키지 ID: `{report['package_id']}`",
        f"- 출제범위: {report['scope_label']}",
        f"- 문제 생성 수행: {report['question_generation_performed']}",
        f"- Harness overall_pass: {summary.get('overall_pass')}",
        f"- Harness errors: {summary.get('error_count')}",
        f"- Harness warnings: {summary.get('warning_count')}",
        "",
        "## 산출물",
        f"- 프롬프트: `{report['outputs']['prompt']}`",
        f"- 패키지 스냅샷: `{report['outputs']['package_snapshot']}`",
        f"- 생성 문항 JSON: `{report['outputs'].get('draft_item', '')}`",
        f"- Harness 보고서: `{report['outputs'].get('validation_report', '')}`",
        "",
        "## 주의",
        "- 이 결과는 문제은행 저장본이 아니라 dry-run 초안입니다.",
        "- reviewed/approved 상태로 전환하려면 추가 검증 에이전트와 전문가 검수가 필요합니다.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages", type=Path, default=DEFAULT_PACKAGES)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--answer-evidence-vector-db", type=Path, default=DEFAULT_ANSWER_EVIDENCE_VECTOR_DB)
    parser.add_argument("--generation-safe-vector-db", type=Path, default=DEFAULT_GENERATION_SAFE_VECTOR_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--package-id", default="")
    parser.add_argument("--package-index", type=int, default=0)
    parser.add_argument("--max-evidence-chars", type=int, default=1200)
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--allow-unsafe-package", action="store_true", help="Allow evidence refs outside generation-safe vector index. Diagnostics only.")
    parser.add_argument("--run-codex", action="store_true", help="Actually call local `codex exec`; otherwise only prepare prompt and metadata.")
    args = parser.parse_args()

    packages = read_jsonl(args.packages)
    package = select_package(packages, args.package_id, args.package_index)
    generation_safe_ids = load_vector_rag_ids(args.generation_safe_vector_db)
    unsafe_refs = package_safe_ref_errors(package, generation_safe_ids)
    if unsafe_refs and not args.allow_unsafe_package:
        raise SystemExit(
            "Package evidence is outside the generation-safe vector index. "
            f"unsafe_count={len(unsafe_refs)} first_ids={unsafe_refs[:10]}"
        )
    scope = package.get("requested_scope") or {}
    scope_label = " / ".join(scope.get(key, "") for key in ["period", "subject", "field", "area", "detail"] if scope.get(key))
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{safe_slug(package.get('package_id', 'package'))}"
    run_dir = args.output_dir / run_id

    conn = sqlite3.connect(args.db)
    try:
        evidence = evidence_for_prompt(package, conn, args.max_evidence_chars)
        prompt = build_prompt(package, evidence)
        prompt_path = run_dir / "prompt.txt"
        package_path = run_dir / "request_package_snapshot.json"
        draft_path = run_dir / "draft_item.json"
        raw_path = run_dir / "codex_last_message.json"
        validation_path = run_dir / "validation_report.json"
        report_path = run_dir / "dry_run_report.json"
        report_md_path = run_dir / "dry_run_report.md"

        write_text(prompt_path, prompt)
        write_json(package_path, package)

        draft_item: dict[str, Any] | None = None
        validation_report: dict[str, Any] = {
            "summary": {
                "overall_pass": None,
                "error_count": None,
                "warning_count": None,
                "finding_count": None,
            },
            "findings": [],
        }
        mode = "prepare_only"
        if args.run_codex:
            mode = "codex_cli"
            draft_item = run_codex(prompt, OUTPUT_SCHEMA, raw_path, args.model, args.timeout)
            write_json(draft_path, draft_item)
            validation_report = validate_item(draft_item, conn, generation_safe_ids)
            write_json(validation_path, validation_report)
    finally:
        conn.close()

    outputs = {
        "prompt": str(prompt_path),
        "package_snapshot": str(package_path),
        "draft_item": str(draft_path) if draft_item is not None else "",
        "raw_codex_last_message": str(raw_path) if draft_item is not None else "",
        "validation_report": str(validation_path) if draft_item is not None else "",
        "dry_run_report": str(report_path),
        "dry_run_report_md": str(report_md_path),
    }
    report = {
        "version": "2026-06-24",
        "created_at": now_iso(),
        "mode": mode,
        "package_id": package.get("package_id"),
        "scope_label": scope_label,
        "question_generation_performed": bool(args.run_codex),
        "codex_model": args.model if args.run_codex else "",
        "inputs": {
            "packages": str(args.packages),
            "db": str(args.db),
            "answer_evidence_vector_db": str(args.answer_evidence_vector_db),
            "generation_safe_vector_db": str(args.generation_safe_vector_db),
            "output_schema": str(OUTPUT_SCHEMA),
        },
        "outputs": outputs,
        "validation_report": validation_report,
        "policy": {
            "final_question_bank_storage": False,
            "rag_use": "answer_evidence_only",
            "must_not_copy_source_text": True,
            "visual_formula_table_law_default_excluded": True,
            "generation_safe_index_enforced": not args.allow_unsafe_package,
            "generation_safe_index_id_count": len(generation_safe_ids),
            "unsafe_package_evidence_refs": unsafe_refs,
        },
    }
    write_json(report_path, report)
    write_text(report_md_path, markdown_summary(report))
    print(json.dumps({"run_dir": str(run_dir), "mode": mode, "validation_summary": validation_report["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
