#!/usr/bin/env python3
"""Build Planner records and generation request packages from SGP v3."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKAGES = PROJECT_ROOT / "resources/generated/safe_generation_packages_v3/safe_generation_packages_v3_ready.jsonl"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources/generated/question_generation_plans_v1"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Expected object at {path}:{line_no}")
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


def stable_id(prefix: str, payload: Any) -> str:
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def option_plan(points: list[str], limit: int = 4) -> list[dict[str, str]]:
    selected = points[:limit]
    rows = []
    labels = ["혼동 개념", "반대 개념", "범주 오류", "과잉 일반화"]
    for idx, point in enumerate(selected):
        rows.append(
            {
                "type": labels[idx % len(labels)],
                "basis": point,
                "scope_rule": "요청 세부영역 안에서 실제 수험자가 혼동할 만한 개념으로 작성",
                "style_rule": "정답 보기와 비슷한 길이와 문체의 평서형 보기로 작성",
                "avoid": "터무니없는 표현, 절대어 남발, 다른 과목·다른 세부영역으로 이탈, 그림·표·영상 전제",
                "exclusion_required": "해설에서 이 오답의 핵심 문구를 짚어 왜 배제되는지 선택지 순서와 대응되게 설명",
                "plausibility_rule": "정답을 단순 부정하거나 쉽게 틀린 보기가 아니라 같은 범주의 그럴듯한 혼동 개념으로 작성",
                "same_category_rule": "정답 보기와 같은 범주, 같은 문법 형태, 비슷한 추상도 안에서 하나의 조건만 어긋나게 작성",
                "copyright_distance_rule": "근거 발췌의 긴 명사구나 문장 배열을 보기 문장에 그대로 옮기지 않음",
            }
        )
    return rows


def explanation_plan(pkg: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": "6문장 이내의 짧은 해설",
        "must_include": [
            "첫 문장에 정답이 되는 핵심 근거를 새 문장으로 설명",
            "나머지 문장에 오답별 배제 이유 4개를 선택지 순서와 대응되게 간결하게 반영",
            "근거 자료라는 메타 표현 사용 금지",
            "보기 번호 대신 보기의 핵심 내용을 직접 언급",
            "오답 배제 문장마다 서로 다른 개념 차이 또는 조건 차이를 설명",
        ],
        "wrong_option_exclusion": True,
        "minimum_exclusion_reasons": 4,
        "wrong_option_sentence_pattern": "각 오답 배제 이유는 보기의 핵심 문구를 주어로 삼고, 같은 표현을 반복하지 않도록 작성",
        "evidence_boundary": [
            "근거에서 직접 확인되는 개념만 사용",
            "오답 배제를 위해 근거 밖 지식이 필요하면 그 오답을 다른 보기로 교체",
            "보류 자료, 그림·표·영상·수치 기준, 법규성 내용으로 설명을 확장하지 않음",
            "근거 발췌의 긴 표현이나 문장 순서를 해설에 그대로 가져오지 않음",
        ],
        "style": {
            "tone": "국가고시 해설처럼 간결하고 단정한 문체",
            "source_distance": "근거 문장을 그대로 옮기거나 어순만 바꾼 문장 금지",
            "avoid": ["정답은 ~번", "위 근거에서", "자료에 따르면", "명백히 틀리다", "말이 안 된다", "정답 조건과 달리 반복"],
        },
        "avoid": pkg.get("forbidden_points") or [],
    }


def build(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    packages = read_jsonl(args.safe_packages)
    plans: list[dict[str, Any]] = []
    request_packages: list[dict[str, Any]] = []

    for pkg in packages:
        limits = pkg.get("generation_limits") or {}
        max_questions = min(int(limits.get("max_questions_per_package") or 1), int(args.max_questions_per_package), 2)
        for variant_no in range(1, max_questions + 1):
            settings = pkg.get("recommended_generation_settings") or {}
            plan = {
                "planner_id": stable_id("plan", {"package_id": pkg.get("package_id"), "variant_no": variant_no}),
                "created_at": now_iso(),
                "safe_package_id": pkg.get("package_id"),
                "knowledge_object_id": pkg.get("knowledge_object_id"),
                "variant_no": variant_no,
                "question_type": settings.get("question_type") or "개념형",
                "difficulty": settings.get("difficulty") or "중",
                "answer_focus": pkg.get("answerable_point") or "",
                "correct_answer_basis": pkg.get("answerable_point") or "",
                "distractor_plan": option_plan(pkg.get("distractor_points") or []),
                "explanation_plan": explanation_plan(pkg),
                "item_style_plan": {
                    "stem": "묻는 초점 하나만 포함하고, 가장 알맞은 것은/옳은 것은 형태의 간결한 문장",
                    "options": "5개 보기는 모두 같은 범주, 비슷한 길이, 같은 문체로 작성",
                    "correct_option": "근거의 핵심 개념을 새 표현으로 압축",
                    "wrong_options": "정답과 같은 범위의 plausible distractor로 작성하되 정답 조건 하나가 어긋나게 구성",
                    "option_homogeneity_gate": "보기 5개가 모두 장비/관리대책/검사방법/개념 정의 중 하나의 동일 범주에 속하지 않으면 다시 작성",
                    "distractor_plausibility_gate": "오답이 정답을 노골적으로 부정하거나 범위 밖 개념이면 같은 범위의 혼동 개념으로 교체",
                    "copyright_distance_gate": "근거 발췌와 같은 긴 어절 배열이 보이면 어순과 표현을 바꿔 새 문장으로 재작성",
                    "avoid": [
                        "정답만 지나치게 길거나 구체적인 보기",
                        "오답만 우스꽝스럽거나 비현실적인 보기",
                        "원문 핵심 문장을 그대로 옮긴 보기",
                        "그림·표·영상·수치 계산 전제",
                        "제외한다, 무시한다, 뒤로 둔다처럼 쉽게 틀린 보기",
                    ],
                },
                "forbidden_points": pkg.get("forbidden_points") or [],
                "policy": {
                    "planner_bounds_generation": True,
                    "text_only_1_2_period": True,
                    "visual_table_formula_law_materials_excluded": True,
                    "source_text_included": False,
                },
            }
            plans.append(plan)

            objective = pkg.get("learning_objective") or {}
            request_packages.append(
                {
                    "package_id": f"planned_{pkg.get('package_id')}_v{variant_no:02d}",
                    "created_at": now_iso(),
                    "mode": "planner_based_safe_generation_package_v3",
                    "package_status": "ready_planner_based",
                    "requested_scope": pkg.get("requested_scope") or {},
                    "evidence_refs": [
                        {
                            "rag_input_id": ref.get("rag_input_id"),
                            "evidence_role": ref.get("evidence_role") or "use_direct",
                        }
                        for ref in (pkg.get("evidence_refs") or [])
                    ],
                    "min_evidence_count": 2,
                    "recommended_generation_settings": {
                        "difficulty_candidates": [plan["difficulty"]],
                        "question_type_candidates": [plan["question_type"]],
                        "learning_objective_candidates": [objective],
                        "required_evidence_types": ["safe_generation_package_v3_use_direct"],
                    },
                    "generation_constraints": {
                        **(pkg.get("generation_constraints") or {}),
                        "approved_for_generation": True,
                        "planner_required": True,
                        "max_questions_per_package": max_questions,
                    },
                    "question_generation_plan": plan,
                    "generation_variant": {
                        "variant_no": variant_no,
                        "source_safe_package_id": pkg.get("package_id"),
                        "source_knowledge_object_id": pkg.get("knowledge_object_id"),
                        "planner_id": plan["planner_id"],
                        "focus_answerable_point": plan["answer_focus"],
                        "distractor_plan": plan["distractor_plan"],
                        "forbidden_points": plan["forbidden_points"],
                    },
                    "rag_index_policy": {
                        "generation_policy": "planner_based_sgp3_use_direct_refs_only",
                        "generation_safe_filter_enabled": True,
                    },
                }
            )

    report = {
        "created_at": now_iso(),
        "inputs": {"safe_packages": str(args.safe_packages)},
        "outputs": {
            "plans": str(args.output_dir / "question_generation_plans.jsonl"),
            "request_packages": str(args.output_dir / "planner_question_request_packages.jsonl"),
            "report_json": str(args.output_dir / "question_generation_plan_report.json"),
            "report_md": str(args.output_dir / "question_generation_plan_report.md"),
        },
        "counts": {
            "safe_packages": len(packages),
            "plans": len(plans),
            "request_packages": len(request_packages),
            "by_question_type": dict(Counter(plan.get("question_type") for plan in plans)),
        },
        "policy": {
            "question_generation_performed": False,
            "max_questions_per_package": min(max(1, args.max_questions_per_package), 2),
            "planner_bounds_generation": True,
        },
    }
    return plans, request_packages, report


def write_markdown(path: Path, report: dict[str, Any], plans: list[dict[str, Any]]) -> None:
    lines = ["# Question Generation Plan Report", "", "## Summary", ""]
    for key, value in report["counts"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Plans", "", "| planner | package | type | difficulty | focus | distractors |", "|---|---|---|---|---|---:|"])
    for plan in plans:
        lines.append(
            f"| {plan.get('planner_id')} | {plan.get('safe_package_id')} | {plan.get('question_type')} | "
            f"{plan.get('difficulty')} | {plan.get('answer_focus')} | {len(plan.get('distractor_plan') or [])} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--safe-packages", type=Path, default=DEFAULT_PACKAGES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--max-questions-per-package", type=int, default=1)
    args = parser.parse_args()
    plans, request_packages, report = build(args)
    write_jsonl(args.output_dir / "question_generation_plans.jsonl", plans)
    write_jsonl(args.output_dir / "planner_question_request_packages.jsonl", request_packages)
    write_json(args.output_dir / "question_generation_plan_report.json", report)
    write_markdown(args.output_dir / "question_generation_plan_report.md", report, plans)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
