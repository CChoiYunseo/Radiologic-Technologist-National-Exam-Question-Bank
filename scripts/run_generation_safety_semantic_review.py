#!/usr/bin/env python3
"""Run LLM semantic review for generation-safety candidate refs.

The review reads source chunk text from the local RAG JSONL and sends it to
Codex CLI for review, but output files contain only metadata, verdicts, and
rag_input_id references. Source excerpts are not persisted.
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
DEFAULT_REBUILD_CANDIDATES = (
    PROJECT_ROOT
    / "resources/generated/safe_generation_package_rebuild_candidates/safe_package_rebuild_candidates.jsonl"
)
DEFAULT_RAG = PROJECT_ROOT / "resources/extracted/rag_index_input/rag_index_input_mapped.jsonl"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources/generated/generation_safety_semantic_review"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
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


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", value).strip("_")
    return slug[:100] or "semantic_review"


def output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["package_verdict", "approved_refs", "held_refs", "notes"],
        "additionalProperties": False,
        "properties": {
            "package_verdict": {"enum": ["pass", "partial", "hold"]},
            "approved_refs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["rag_input_id", "reason"],
                    "additionalProperties": False,
                    "properties": {
                        "rag_input_id": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                },
            },
            "held_refs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["rag_input_id", "hold_reason"],
                    "additionalProperties": False,
                    "properties": {
                        "rag_input_id": {"type": "string"},
                        "hold_reason": {"type": "string"},
                    },
                },
            },
            "notes": {"type": "string"},
        },
    }


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
        raise ValueError("LLM output is not an object")
    return value


def run_codex(prompt: str, schema_path: Path, raw_output: Path, model: str, timeout: int) -> dict[str, Any]:
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
        str(schema_path),
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


def build_prompt(package: dict[str, Any], rag_by_id: dict[str, dict[str, Any]], max_chars: int) -> str:
    scope = package.get("scope") or {}
    refs = []
    for ref in (package.get("candidate_refs") or [])[: package.get("_max_refs", 4)]:
        rag_input_id = ref.get("rag_input_id")
        row = rag_by_id.get(str(rag_input_id)) or {}
        content = " ".join(str(row.get("content") or "").split())[:max_chars]
        refs.append(
            {
                "rag_input_id": rag_input_id,
                "source_file": ref.get("source_file"),
                "page_or_slide": ref.get("page_or_slide"),
                "scope_mapping_confidence": ref.get("scope_mapping_confidence"),
                "content_for_review": content,
            }
        )
    payload = {
        "task": "RAG chunk의 자동 문제 생성 근거 승격 가능성 검수",
        "scope": scope,
        "candidate_refs": refs,
        "rules": {
            "approve_only_if": [
                "본문 텍스트만으로 해당 출제범위의 새 문항 근거가 된다",
                "표, 그림, 수식, 법규 조문, 최신 수치 기준에 의존하지 않는다",
                "OCR 손상이 심하지 않아 의미 판단이 가능하다",
                "원문 문장 복사 없이 새 문장 문항 생성이 가능하다",
            ],
            "hold_if": [
                "그림/표/수식/법규/수치 기준에 의존한다",
                "OCR 손상 때문에 의미가 불명확하다",
                "출제범위와 직접 맞지 않는다",
                "정답 근거로 쓰기에는 문맥이 참고문헌/목차/색인에 가깝다",
            ],
            "output_must_not_include_source_text": True,
        },
    }
    return (
        "너는 방사선사 국가고시 문제은행의 근거 승격 검수자다.\n"
        "출력에는 원문 문구나 발췌문을 절대 포함하지 말고 rag_input_id와 판정 이유만 쓴다.\n"
        "JSON object만 출력한다.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def build(args: argparse.Namespace) -> dict[str, Any]:
    candidates = read_jsonl(args.rebuild_candidates)
    rag_by_id = {str(row.get("rag_input_id")): row for row in read_jsonl(args.rag)}
    selected = candidates[args.offset : args.offset + args.limit]

    run_dir = args.output_dir / datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)
    schema_path = run_dir / "semantic_review_output_schema.json"
    write_json(schema_path, output_schema())

    results: list[dict[str, Any]] = []
    approved_package_candidates: list[dict[str, Any]] = []
    for package in selected:
        package = dict(package)
        package["_max_refs"] = args.max_refs
        item_dir = run_dir / safe_slug(package.get("package_rebuild_id") or "package")
        raw_path = item_dir / "codex_last_message.json"
        result_path = item_dir / "semantic_review_result.json"
        snapshot_path = item_dir / "package_snapshot_without_source_text.json"
        package.pop("_max_refs", None)
        write_json(snapshot_path, package)
        row = {
            "package_rebuild_id": package.get("package_rebuild_id"),
            "scope": package.get("scope"),
            "status": "prepared",
            "semantic_review_result": str(result_path),
            "package_snapshot": str(snapshot_path),
            "approved_ref_count": 0,
            "held_ref_count": 0,
            "error": "",
        }
        try:
            package["_max_refs"] = args.max_refs
            if args.run_codex:
                result = run_codex(
                    build_prompt(package, rag_by_id, args.max_chars),
                    schema_path,
                    raw_path,
                    args.model,
                    args.timeout,
                )
                write_json(result_path, result)
                row["status"] = result.get("package_verdict") or "completed"
                row["approved_ref_count"] = len(result.get("approved_refs") or [])
                row["held_ref_count"] = len(result.get("held_refs") or [])
                if row["approved_ref_count"] >= args.min_refs_to_pass:
                    approved_package_candidates.append(
                        {
                            "package_rebuild_id": package.get("package_rebuild_id"),
                            "status": "semantic_review_pass_pending_package_rebuild",
                            "scope": package.get("scope"),
                            "learning_objective": package.get("learning_objective"),
                            "approved_refs": result.get("approved_refs") or [],
                            "source_text_included": False,
                        }
                    )
            else:
                write_json(result_path, {"package_verdict": "prepared", "approved_refs": [], "held_refs": [], "notes": ""})
        except Exception as exc:
            row["status"] = "error"
            row["error"] = str(exc)[-3000:]
            write_json(result_path, {"package_verdict": "error", "approved_refs": [], "held_refs": [], "notes": row["error"]})
        results.append(row)

    results_path = run_dir / "semantic_review_results.jsonl"
    approved_path = run_dir / "semantic_review_pass_package_candidates.jsonl"
    report_json_path = run_dir / "semantic_review_batch_report.json"
    report_md_path = run_dir / "semantic_review_batch_report.md"
    write_jsonl(results_path, results)
    write_jsonl(approved_path, approved_package_candidates)
    counts = {
        "selected_packages": len(selected),
        "reviewed_or_prepared": len(results),
        "errors": sum(1 for row in results if row.get("status") == "error"),
        "semantic_review_pass_package_candidates": len(approved_package_candidates),
    }
    report = {
        "created_at": now_iso(),
        "mode": "codex_cli" if args.run_codex else "prepare_only",
        "inputs": {
            "rebuild_candidates": str(args.rebuild_candidates),
            "rag": str(args.rag),
            "offset": args.offset,
            "limit": args.limit,
        },
        "outputs": {
            "run_dir": str(run_dir),
            "results": str(results_path),
            "semantic_review_pass_package_candidates": str(approved_path),
            "report_json": str(report_json_path),
            "report_md": str(report_md_path),
        },
        "counts": counts,
        "policy": {
            "source_text_included_in_outputs": False,
            "automatic_generation_approval_granted": False,
            "question_generation_performed": False,
        },
    }
    write_json(report_json_path, report)
    write_markdown(report_md_path, report, results)
    return report


def write_markdown(path: Path, report: dict[str, Any], results: list[dict[str, Any]]) -> None:
    lines = [
        "# Generation Safety Semantic Review Batch",
        "",
        f"- mode: {report['mode']}",
        f"- selected_packages: {report['counts']['selected_packages']}",
        f"- semantic_review_pass_package_candidates: {report['counts']['semantic_review_pass_package_candidates']}",
        f"- errors: {report['counts']['errors']}",
        "",
        "## Results",
        "",
        "| package_rebuild_id | status | approved refs | held refs |",
        "|---|---|---:|---:|",
    ]
    for row in results:
        lines.append(
            f"| {row.get('package_rebuild_id')} | {row.get('status')} | {row.get('approved_ref_count')} | {row.get('held_ref_count')} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild-candidates", type=Path, default=DEFAULT_REBUILD_CANDIDATES)
    parser.add_argument("--rag", type=Path, default=DEFAULT_RAG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--max-refs", type=int, default=4)
    parser.add_argument("--max-chars", type=int, default=1000)
    parser.add_argument("--min-refs-to-pass", type=int, default=2)
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--run-codex", action="store_true")
    args = parser.parse_args()
    report = build(args)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
