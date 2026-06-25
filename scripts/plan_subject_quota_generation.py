#!/usr/bin/env python3
"""Plan subject-quota question generation packages.

This script does not call Codex and does not store questions. It expands safe
request packages into a deterministic subject-quota batch plan, accounting for
existing question-bank candidates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from generate_question_dry_run import DEFAULT_PACKAGES, package_is_default_safe


DEFAULT_TARGETS = PROJECT_ROOT / "resources" / "rules" / "question_batch_targets.json"
DEFAULT_CANDIDATE_DB = (
    PROJECT_ROOT
    / "resources"
    / "generated"
    / "question_bank_candidates"
    / "question_bank_candidates.sqlite"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources" / "generated" / "subject_quota_generation_plans"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def short_hash(value: Any, length: int = 16) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def candidate_counts(db_path: Path, statuses: list[str]) -> dict[str, int]:
    if not db_path.exists():
        return {}
    placeholders = ",".join("?" for _ in statuses)
    query = (
        "SELECT subject, COUNT(*) AS count FROM question_bank_candidate "
        f"WHERE status IN ({placeholders}) GROUP BY subject"
    )
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(query, statuses).fetchall()
    finally:
        conn.close()
    return {str(subject): int(count) for subject, count in rows}


def safe_question_types(package: dict[str, Any], defaults: list[str]) -> list[str]:
    settings = package.get("recommended_generation_settings") or {}
    qtypes = [str(item) for item in settings.get("question_type_candidates") or []]
    allowed = [item for item in qtypes if item in defaults]
    return allowed or [defaults[0]]


def safe_difficulties(package: dict[str, Any], defaults: list[str]) -> list[str]:
    settings = package.get("recommended_generation_settings") or {}
    values = [str(item) for item in settings.get("difficulty_candidates") or []]
    allowed = [item for item in defaults if item in values]
    return allowed or [defaults[0]]


def objective_candidates(package: dict[str, Any]) -> list[dict[str, Any]]:
    settings = package.get("recommended_generation_settings") or {}
    rows = settings.get("learning_objective_candidates") or []
    return [row for row in rows if isinstance(row, dict)] or [{}]


def build_variant_package(
    package: dict[str, Any],
    *,
    plan_id: str,
    subject: str,
    sequence: int,
    target_count: int,
    variant_index: int,
    question_type: str,
    difficulty: str,
    focus_angle: str,
    learning_objective: dict[str, Any],
) -> dict[str, Any]:
    base_id = str(package.get("package_id") or "package")
    variant_key = {
        "plan_id": plan_id,
        "base_package_id": base_id,
        "subject": subject,
        "sequence": sequence,
        "variant_index": variant_index,
        "question_type": question_type,
        "difficulty": difficulty,
        "learning_objective_id": learning_objective.get("learning_objective_id", ""),
        "focus_angle": focus_angle,
    }
    cloned = json.loads(json.dumps(package, ensure_ascii=False))
    cloned["base_package_id"] = base_id
    cloned["package_id"] = "qrp_subjectquota_" + short_hash(variant_key, 18)
    cloned["mode"] = "subject_quota_generation_request"
    settings = cloned.setdefault("recommended_generation_settings", {})
    settings["question_type_candidates"] = [question_type]
    settings["difficulty_candidates"] = [difficulty]
    settings["learning_objective_candidates"] = [learning_objective] if learning_objective else []
    cloned["generation_variant"] = {
        "plan_id": plan_id,
        "target_subject": subject,
        "target_count": target_count,
        "subject_sequence": sequence,
        "base_package_id": base_id,
        "variant_index": variant_index,
        "focus_angle": focus_angle,
        "question_type": question_type,
        "difficulty": difficulty,
        "instruction": (
            "같은 범위에서 반복 생성하더라도 문제 줄기, 보기 구성, 오답 포인트를 "
            "이전 변형과 다르게 만든다. 원문 문장 재현은 금지한다."
        ),
    }
    return cloned


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# 과목별 50문항 생성 계획",
        "",
        f"- 생성 시각: {report['created_at']}",
        f"- 계획 ID: `{report['plan_id']}`",
        f"- 입력 패키지: `{report['inputs']['packages']}`",
        f"- 확장 패키지 수: {report['counts']['planned_generation_packages']}",
        "",
        "## 과목별 계획",
    ]
    for row in report["subject_plan"]:
        lines.append(
            f"- {row['subject']}: 목표 {row['target_count']}개, 기존 {row['existing_count']}개, "
            f"추가 필요 {row['missing_count']}개, 안전 패키지 {row['safe_package_count']}개"
        )
    lines.extend(
        [
            "",
            "## 다음 실행",
            "```bash",
            report["commands"]["generate_prepare"],
            report["commands"]["generate_run_codex"],
            report["commands"]["build_review_candidates"],
            report["commands"]["run_llm_secondary_validation_prepare"],
            report["commands"]["run_llm_secondary_validation_codex"],
            "```",
            "",
            "## 주의",
            "- 이 계획은 생성 후보를 준비하는 단계이며 학생 화면에 직접 공개하지 않습니다.",
            "- Codex CLI 실행 후 Harness와 2차 검증을 통과한 항목만 전문가 검수 후보로 이동해야 합니다.",
            "- 법규·수치·수식·표·그림 기반 문항은 기본 제외 정책을 유지합니다.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages", type=Path, default=DEFAULT_PACKAGES)
    parser.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    parser.add_argument("--candidate-db", type=Path, default=DEFAULT_CANDIDATE_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--plan-id", default="")
    args = parser.parse_args()

    targets = read_json(args.targets)
    packages = read_jsonl(args.packages)
    statuses = [str(item) for item in targets.get("count_existing_statuses") or ["pending_expert_review", "expert_passed"]]
    current_counts = candidate_counts(args.candidate_db, statuses)
    defaults = targets.get("default_generation") or {}
    default_qtypes = [str(item) for item in defaults.get("question_types") or ["개념형"]]
    default_difficulties = [str(item) for item in defaults.get("difficulties") or ["중"]]
    focus_angles = [str(item) for item in defaults.get("focus_angles") or ["핵심 개념 확인"]]

    safe_packages = [package for package in packages if package_is_default_safe(package)]
    packages_by_subject: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for package in safe_packages:
        subject = ((package.get("requested_scope") or {}).get("subject") or "").strip()
        if subject:
            packages_by_subject[subject].append(package)

    plan_id = args.plan_id or "subject_quota_" + run_id()
    plan_dir = args.output_dir / plan_id
    expanded_packages: list[dict[str, Any]] = []
    subject_plan: list[dict[str, Any]] = []

    for target in targets.get("targets") or []:
        subject = str(target.get("subject") or "").strip()
        target_count = int(target.get("target_count") or 0)
        existing_count = int(current_counts.get(subject, 0))
        missing_count = max(target_count - existing_count, 0)
        subject_packages = packages_by_subject.get(subject, [])
        if not subject or target_count <= 0:
            continue
        if missing_count > 0 and not subject_packages:
            raise SystemExit(f"No safe request packages for subject: {subject}")

        for i in range(missing_count):
            base_package = subject_packages[i % len(subject_packages)]
            variant_index = i // len(subject_packages)
            qtypes = safe_question_types(base_package, default_qtypes)
            difficulties = safe_difficulties(base_package, default_difficulties)
            objectives = objective_candidates(base_package)
            variant = build_variant_package(
                base_package,
                plan_id=plan_id,
                subject=subject,
                sequence=i + 1,
                target_count=target_count,
                variant_index=variant_index,
                question_type=qtypes[i % len(qtypes)],
                difficulty=difficulties[(i // max(len(qtypes), 1)) % len(difficulties)],
                focus_angle=focus_angles[i % len(focus_angles)],
                learning_objective=objectives[i % len(objectives)],
            )
            expanded_packages.append(variant)

        subject_plan.append(
            {
                "subject": subject,
                "target_count": target_count,
                "existing_count": existing_count,
                "missing_count": missing_count,
                "safe_package_count": len(subject_packages),
            }
        )

    expanded_path = plan_dir / "question_request_packages_subject_quota.jsonl"
    report_json = plan_dir / "subject_quota_generation_plan_report.json"
    report_md = plan_dir / "subject_quota_generation_plan_report.md"
    command_path = plan_dir / "run_next_steps.sh"
    full_pipeline_path = plan_dir / "run_full_generation_pipeline.sh"
    review_dir = plan_dir / "review_candidates"
    llm_dir = plan_dir / "llm_secondary_validation_runs"
    draft_output_dir = plan_dir / "question_drafts"
    batch_dir_hint = f"{draft_output_dir}/batch_<created>_limit{len(expanded_packages)}_offset0"
    passed_hint = f"{batch_dir_hint}/passed_drafts_index.jsonl"

    generate_prepare = (
        f"python3 {PROJECT_ROOT}/scripts/generate_question_batch.py "
        f"--packages {expanded_path} --output-dir {draft_output_dir} "
        f"--limit {len(expanded_packages)} --offset 0"
    )
    generate_run = generate_prepare + " --run-codex --model gpt-5.5 --timeout 300"
    build_review = (
        f"python3 {PROJECT_ROOT}/scripts/build_review_candidate_index.py "
        f"--passed-index {passed_hint} --output-dir {review_dir}"
    )
    llm_prepare = (
        f"python3 {PROJECT_ROOT}/scripts/run_llm_secondary_validation_batch.py "
        f"--packages {review_dir}/llm_secondary_validation_packages.jsonl "
        f"--review-index {review_dir}/review_candidate_index.jsonl "
        f"--output-dir {llm_dir} --limit 0 --offset 0"
    )
    llm_run = llm_prepare + " --run-codex --model gpt-5.5 --timeout 300"

    report = {
        "version": "2026-06-25",
        "created_at": now_iso(),
        "plan_id": plan_id,
        "inputs": {
            "packages": str(args.packages),
            "targets": str(args.targets),
            "candidate_db": str(args.candidate_db),
            "count_existing_statuses": statuses,
        },
        "outputs": {
            "expanded_packages": str(expanded_path),
            "report_json": str(report_json),
            "report_md": str(report_md),
            "command_file": str(command_path),
            "full_pipeline_script": str(full_pipeline_path),
        },
        "counts": {
            "input_packages": len(packages),
            "safe_packages": len(safe_packages),
            "planned_generation_packages": len(expanded_packages),
            "by_subject": dict(Counter((row.get("requested_scope") or {}).get("subject", "") for row in expanded_packages)),
        },
        "subject_plan": subject_plan,
        "commands": {
            "generate_prepare": generate_prepare,
            "generate_run_codex": generate_run,
            "build_review_candidates": build_review,
            "run_llm_secondary_validation_prepare": llm_prepare,
            "run_llm_secondary_validation_codex": llm_run,
        },
        "policy": targets.get("policy") or {},
    }

    write_jsonl(expanded_path, expanded_packages)
    write_json(report_json, report)
    write_text(report_md, markdown_report(report))
    write_text(command_path, "#!/usr/bin/env bash\nset -euo pipefail\n\n" + "\n\n".join(report["commands"].values()) + "\n")
    command_path.chmod(0o755)
    full_pipeline = f"""#!/usr/bin/env bash
set -euo pipefail

PLAN_DIR="{plan_dir}"
GENERATE_RESULT="$PLAN_DIR/full_generation_result.json"
REVIEW_DIR="$PLAN_DIR/review_candidates_full"
LLM_DIR="$PLAN_DIR/llm_secondary_validation_runs_full"

python3 {PROJECT_ROOT}/scripts/generate_question_batch.py \\
  --packages "$PLAN_DIR/question_request_packages_subject_quota.jsonl" \\
  --output-dir "$PLAN_DIR/question_drafts_full" \\
  --limit {len(expanded_packages)} \\
  --offset 0 \\
  --run-codex \\
  --model gpt-5.5 \\
  --timeout 300 | tee "$GENERATE_RESULT"

BATCH_DIR=$(python3 - "$GENERATE_RESULT" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["batch_dir"])
PY
)

python3 {PROJECT_ROOT}/scripts/build_review_candidate_index.py \\
  --passed-index "$BATCH_DIR/passed_drafts_index.jsonl" \\
  --output-dir "$REVIEW_DIR"

python3 {PROJECT_ROOT}/scripts/run_llm_secondary_validation_batch.py \\
  --packages "$REVIEW_DIR/llm_secondary_validation_packages.jsonl" \\
  --review-index "$REVIEW_DIR/review_candidate_index.jsonl" \\
  --output-dir "$LLM_DIR" \\
  --limit 0 \\
  --offset 0 \\
  --run-codex \\
  --model gpt-5.5 \\
  --timeout 300
"""
    write_text(full_pipeline_path, full_pipeline)
    full_pipeline_path.chmod(0o755)

    print(json.dumps({"plan_dir": str(plan_dir), "counts": report["counts"], "subject_plan": subject_plan}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
