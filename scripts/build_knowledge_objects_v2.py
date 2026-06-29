#!/usr/bin/env python3
"""Build Knowledge Objects v2 from semantic-reviewed safe refs and feedback.

Knowledge Objects are minimum answerable units for question generation. This
script emits metadata and refs only; it does not copy textbook body text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEMANTIC_PASS = (
    PROJECT_ROOT
    / "resources/generated/folder_detail_generation_safety_semantic_review/run_20260629T003736Z"
    / "semantic_review_pass_package_candidates_enriched.jsonl"
)
DEFAULT_IMPROVEMENT = PROJECT_ROOT / "resources/generated/package_quality_feedback_v2/package_improvement_worklist.jsonl"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources/generated/knowledge_objects_v2"

DOMAIN_POLICY: dict[str, dict[str, list[str]]] = {
    "방사선관리": {
        "allowed": ["개념", "용어", "관리 원칙", "절차적 판단"],
        "hold": ["법규", "최신 기준", "선량 한도", "계산", "표준값", "표 기반 단위 목록"],
    },
    "초음파기술": {
        "allowed": ["물리", "감쇠", "반사", "굴절", "기본 해부 개념"],
        "hold": ["영상 판독형", "장비 세부 설정", "최신 기능"],
    },
    "전산화단층검사": {
        "allowed": ["원리", "영상 재구성", "장치 구성", "화질 특성"],
        "hold": ["장비 점검 절차", "제조사별 기능", "수치 기준"],
    },
}

DISTRACTOR_PRESETS: dict[str, list[str]] = {
    "방사선관리": [
        "선량명과 단위의 대응을 혼동",
        "방사선 방어 개념과 행정 절차를 혼동",
        "외부피폭과 내부피폭 평가 맥락을 혼동",
        "방사능과 인체 영향 평가량을 혼동",
    ],
    "초음파기술": [
        "해부학적 위치와 인접 구조를 혼동",
        "정상 구조 설명과 병적 소견 설명을 혼동",
        "국소 구조를 장기 전체 특징처럼 일반화",
        "검사 원리와 영상 판독 소견을 혼동",
    ],
    "전산화단층검사": [
        "장치 구성 요소와 영상 재구성 단계를 혼동",
        "원자료 재구성과 영상 후처리를 혼동",
        "화질 인자와 선량 인자를 혼동",
    ],
}

FORBIDDEN_BASE = [
    "원문 문장, 기존 문제, 보기, 해설을 그대로 사용하지 않는다.",
    "표·그림·도표·영상 제시를 전제로 하지 않는다.",
    "법규·최신 수치·공식·표 기반 기준은 확인 전 자동 생성하지 않는다.",
    "RAG 근거는 정답 근거 확인용으로만 사용한다.",
]


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


def ensure_scope_id(scope: dict[str, Any]) -> str:
    if scope.get("scope_id"):
        return str(scope.get("scope_id"))
    return stable_id(
        "folder_scope",
        {key: scope.get(key) or "" for key in ["period", "subject", "field", "area", "detail"]},
    )


def source_rebuild_id(value: str) -> str:
    prefix = "semantic_pilot_"
    if value.startswith(prefix):
        value = value[len(prefix) :]
    if "_v" in value:
        value = value.rsplit("_v", 1)[0]
    return value


def compact(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", str(value or "")).lower()


def improvement_index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("source_package_rebuild_id") or ""): row for row in rows if row.get("source_package_rebuild_id")}


def scope_key(scope: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return tuple(str(scope.get(key) or "") for key in ["period", "subject", "field", "area", "detail"])  # type: ignore[return-value]


def normalize_objective(row: dict[str, Any]) -> dict[str, Any]:
    objective = dict(row.get("learning_objective") or {})
    objective_id = objective.get("learning_objective_id") or objective.get("objective_id")
    return {
        "learning_objective_id": objective_id or "",
        "objective_id": objective_id or "",
        "objective": objective.get("objective") or "",
        "level": objective.get("level") or "",
        "major_unit": objective.get("major_unit") or "",
        "unit": objective.get("unit") or "",
        "mapping_method": objective.get("mapping_method") or "semantic_review_pass",
    }


def concept_tag(scope: dict[str, Any], objective: dict[str, Any]) -> str:
    detail = str(scope.get("detail") or scope.get("area") or "세부 개념")
    objective_text = str(objective.get("objective") or "")
    if "정상" in objective_text and "간" in objective_text:
        return "정상 간의 해부학적 위치와 인접 구조"
    if "단위" in objective_text or "선량" in objective_text:
        return "방사선방어 선량 개념과 단위 구분"
    return detail


def answerable_point(scope: dict[str, Any], objective: dict[str, Any], feedback: dict[str, Any]) -> str:
    tag = concept_tag(scope, objective)
    if feedback.get("package_quality_action") == "requires_use_direct_ref_review":
        return f"{tag} 중 직접 근거가 확인된 부분만 좁혀 묻는다."
    return f"{tag}에 대해 정답 하나로 판단 가능한 핵심 설명을 묻는다."


def forbidden_points(scope: dict[str, Any], feedback: dict[str, Any]) -> list[str]:
    area = str(scope.get("area") or "")
    items = list(FORBIDDEN_BASE)
    policy = DOMAIN_POLICY.get(area)
    if policy:
        items.extend(f"{area} 보류: {item}" for item in policy.get("hold", []))
    items.extend(feedback.get("forbidden_points_to_add") or [])
    return sorted(dict.fromkeys(items))


def distractor_points(scope: dict[str, Any], feedback: dict[str, Any]) -> list[str]:
    area = str(scope.get("area") or "")
    points = list(DISTRACTOR_PRESETS.get(area, []))
    checks = feedback.get("check_counts") or {}
    if checks.get("distractor_quality"):
        points.append("지나치게 명백한 절대 표현 대신 실제 혼동 가능한 인접 개념 사용")
    if checks.get("korean_item_style"):
        points.append("보기의 문장 구조와 범주를 서로 균질하게 유지")
    return sorted(dict.fromkeys(points))[:6]


def ref_quality(ref: dict[str, Any], feedback: dict[str, Any]) -> str:
    if feedback.get("use_direct_policy") == "recheck_or_downgrade":
        return "use_supporting"
    return "use_direct"


def readiness(use_direct: list[dict[str, Any]], distractors: list[str], feedback: dict[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    action = feedback.get("package_quality_action")
    if action in {"hold_until_evidence_replaced", "requires_use_direct_ref_review"}:
        reasons.append(action)
    if len(use_direct) < 2:
        reasons.append("use_direct_refs_lt_2")
    if len(distractors) < 3:
        reasons.append("distractor_points_lt_3")
    if reasons:
        return "needs_rework", reasons
    return "ready", []


def build(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    semantic_rows = read_jsonl(args.semantic_pass)
    feedback_by_id = improvement_index(read_jsonl(args.improvement_worklist))
    objects: list[dict[str, Any]] = []
    chunk_quality: list[dict[str, Any]] = []

    for row in semantic_rows:
        rebuild_id = str(row.get("package_rebuild_id") or "")
        scope = dict(row.get("scope") or {})
        scope["scope_id"] = ensure_scope_id(scope)
        objective = normalize_objective(row)
        feedback = feedback_by_id.get(rebuild_id, {})
        direct_refs: list[dict[str, Any]] = []
        support_refs: list[dict[str, Any]] = []
        for ref in row.get("approved_refs") or []:
            quality = ref_quality(ref, feedback)
            ref_record = {
                "rag_input_id": ref.get("rag_input_id"),
                "source_package_rebuild_id": rebuild_id,
                "scope": scope,
                "quality_label": quality,
                "quality_reason": "semantic_review_approved_ref" if quality == "use_direct" else "llm_secondary_requested_direct_ref_recheck",
                "source_text_included": False,
            }
            chunk_quality.append(ref_record)
            clean_ref = {
                "rag_input_id": ref.get("rag_input_id"),
                "evidence_role": quality,
                "review_reason": ref.get("reason") or "",
            }
            if quality == "use_direct":
                direct_refs.append(clean_ref)
            else:
                support_refs.append(clean_ref)

        distractors = distractor_points(scope, feedback)
        status, hold_reasons = readiness(direct_refs, distractors, feedback)
        ko_payload = {"source_package_rebuild_id": rebuild_id, "scope": scope, "objective": objective, "tag": concept_tag(scope, objective)}
        objects.append(
            {
                "knowledge_object_id": stable_id("ko2", ko_payload),
                "created_at": now_iso(),
                "version": "v2",
                "source_package_rebuild_id": rebuild_id,
                "scope": scope,
                "learning_objective": objective,
                "objective_link_confidence": "weak" if feedback.get("objective_link_confidence_delta") == "lower" else "direct",
                "content_tag": concept_tag(scope, objective),
                "answerable_point": answerable_point(scope, objective, feedback),
                "use_direct_refs": direct_refs,
                "use_supporting_refs": support_refs,
                "distractor_points": distractors,
                "forbidden_points": forbidden_points(scope, feedback),
                "domain_policy": DOMAIN_POLICY.get(str(scope.get("area") or ""), {}),
                "review_feedback_summary": {
                    "package_quality_action": feedback.get("package_quality_action") or "keep_ready",
                    "check_counts": feedback.get("check_counts") or {},
                    "recommended_actions": feedback.get("recommended_actions") or {},
                },
                "generation_readiness": status,
                "hold_reasons": hold_reasons,
                "source_text_included": False,
                "question_generation_performed": False,
            }
        )

    report = {
        "created_at": now_iso(),
        "inputs": {"semantic_pass": str(args.semantic_pass), "improvement_worklist": str(args.improvement_worklist)},
        "outputs": {
            "knowledge_objects": str(args.output_dir / "knowledge_objects_v2.jsonl"),
            "chunk_quality": str(args.output_dir / "semantic_chunk_quality_index.jsonl"),
            "report_json": str(args.output_dir / "knowledge_objects_v2_report.json"),
            "report_md": str(args.output_dir / "knowledge_objects_v2_report.md"),
        },
        "counts": {
            "semantic_pass_packages": len(semantic_rows),
            "knowledge_objects": len(objects),
            "chunk_quality_rows": len(chunk_quality),
            "readiness": dict(Counter(obj["generation_readiness"] for obj in objects)),
            "scopes": dict(Counter(" / ".join(scope_key(obj["scope"])) for obj in objects)),
        },
        "policy": {
            "source_text_included": False,
            "question_generation_performed": False,
            "ready_requires_use_direct_refs_gte_2": True,
            "ready_requires_distractor_points_gte_3": True,
        },
    }
    return objects, chunk_quality, report


def write_markdown(path: Path, report: dict[str, Any], objects: list[dict[str, Any]]) -> None:
    lines = ["# Knowledge Objects v2 Report", "", "## Summary", ""]
    for key, value in report["counts"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Objects", "", "| object | readiness | scope | tag | direct | support | hold reasons |", "|---|---|---|---|---:|---:|---|"])
    for obj in objects:
        scope = obj.get("scope") or {}
        scope_label = " / ".join(str(scope.get(key) or "") for key in ["period", "subject", "field", "area", "detail"] if scope.get(key))
        lines.append(
            f"| {obj.get('knowledge_object_id')} | {obj.get('generation_readiness')} | {scope_label} | {obj.get('content_tag')} | "
            f"{len(obj.get('use_direct_refs') or [])} | {len(obj.get('use_supporting_refs') or [])} | {', '.join(obj.get('hold_reasons') or [])} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--semantic-pass", type=Path, default=DEFAULT_SEMANTIC_PASS)
    parser.add_argument("--improvement-worklist", type=Path, default=DEFAULT_IMPROVEMENT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    objects, chunk_quality, report = build(args)
    write_jsonl(args.output_dir / "knowledge_objects_v2.jsonl", objects)
    write_jsonl(args.output_dir / "semantic_chunk_quality_index.jsonl", chunk_quality)
    write_json(args.output_dir / "knowledge_objects_v2_report.json", report)
    write_markdown(args.output_dir / "knowledge_objects_v2_report.md", report, objects)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
