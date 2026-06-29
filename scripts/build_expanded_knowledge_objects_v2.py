#!/usr/bin/env python3
"""Expand Knowledge Object v2 candidates from folder-authoritative safe chunks.

This builder breaks the old "one detail -> one package" bottleneck by grouping
safe text chunks by detail and learning objective, then splitting large groups
into two-ref Knowledge Objects. Outputs contain metadata and refs only; source
body text is used for scoring but is not persisted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SAFE_CHUNKS = (
    PROJECT_ROOT
    / "resources/generated/folder_authoritative_detail_review/folder_detail_review_safe_text_priority.jsonl"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources/generated/knowledge_objects_v2_expanded"

DETAIL_HINTS: dict[str, list[str]] = {
    "공중보건총론": ["공중보건", "건강", "지역사회", "예방"],
    "환경보건": ["환경", "수질", "대기", "폐기물", "식품", "산업", "근로자"],
    "역학 및 질병관리": ["역학", "감염", "감염병", "유행", "전파", "질병관리"],
    "초음파 물리": ["초음파", "주파수", "파장", "감쇠", "반사", "굴절"],
    "발생원리": ["압전", "공진", "진동자", "발생", "탐촉자"],
    "탐촉자": ["탐촉자", "probe", "주파수", "배열"],
    "진단장치": ["장치", "진단장치", "표시장치", "송신", "수신"],
    "도플러 초음파 검사": ["도플러", "혈류", "주파수 편이"],
    "상복부 초음파 검사": ["상복부", "간", "담낭", "췌장", "비장"],
    "표재성장기 초음파검사": ["갑상샘", "유방", "표재", "고환"],
    "CT 기초이론": ["ct", "단층", "재구성", "검출기", "gantry", "스캔"],
    "각종 방사선 검출기의 원리 및 특성": ["검출기", "전리함", "섬광", "반도체", "계수관"],
    "방사선 계측기의 교정 및 계측치의 통계": ["교정", "통계", "계수", "표준편차"],
    "방사선관리의 개념": ["방사선관리", "방어", "피폭", "관리구역"],
    "SPECT 및 PET 시스템": ["spect", "pet", "동시계수", "단층"],
    "섬광카메라 및 핵의학검사 관련기기": ["섬광카메라", "감마카메라", "collimator", "검출기"],
    "핵의학 기기의 성능평가": ["성능평가", "균일도", "분해능", "정도관리"],
}

AREA_DISTRACTORS: dict[str, list[str]] = {
    "공중보건": [
        "개인 건강관리와 지역사회 보건 개념을 혼동",
        "역학 지표와 질병관리 절차를 혼동",
        "환경보건 요인과 감염관리 요인을 혼동",
        "예방 단계와 보건관리 대상을 혼동",
    ],
    "초음파기술": [
        "주파수, 파장, 투과심도의 관계를 혼동",
        "반사, 굴절, 산란, 감쇠를 같은 현상으로 혼동",
        "검사 원리와 영상 판독 소견을 혼동",
        "정상 구조와 병적 소견을 혼동",
    ],
    "전산화단층검사": [
        "장치 구성 요소와 영상 재구성 단계를 혼동",
        "원자료 재구성과 영상 후처리를 혼동",
        "화질 인자와 선량 인자를 혼동",
    ],
    "방사선계측": [
        "검출기 원리와 선량 단위를 혼동",
        "계측기 교정과 측정값 통계를 혼동",
        "조사선량과 흡수선량을 혼동",
    ],
    "핵의학 기기": [
        "감마카메라와 PET 시스템의 검출 원리를 혼동",
        "콜리메이터 역할과 검출기 역할을 혼동",
        "영상 장비 구조와 성능평가 항목을 혼동",
    ],
    "방사선영상": [
        "검사 자세와 투사 방향을 혼동",
        "부위별 해부학적 기준점과 촬영 목적을 혼동",
        "영상 평가 요소와 촬영 조건을 혼동",
    ],
    "투시조영검사": [
        "검사 목적과 조영제 사용 맥락을 혼동",
        "투시 절차와 일반 촬영 절차를 혼동",
        "검사 전 확인 사항과 검사 후 관찰 사항을 혼동",
    ],
    "의료영상정보": [
        "영상 생성 과정과 영상 평가 지표를 혼동",
        "디지털 영상 저장과 영상 처리 과정을 혼동",
        "기록계 특성과 검출기 특성을 혼동",
    ],
    "방사선관리": [
        "방사선 방어 원칙과 행정 절차를 혼동",
        "개인 모니터링과 작업환경 모니터링을 혼동",
        "관리구역 개념과 선량한도 개념을 혼동",
    ],
}

HARD_HOLD_PATTERN = re.compile(
    r"(법규|법률|조문|시행령|시행규칙|고시|선량한도|허용선량|"
    r"공식|계산|표준값|최신|제\s*\d+\s*조)",
    re.IGNORECASE,
)
VISUAL_HOLD_PATTERN = re.compile(r"(그림|표|도표|영상\s*(?:판독|해석|제시)|수식)", re.IGNORECASE)
NUMERIC_RISK_PATTERN = re.compile(
    r"(\d+\s*(?:%|mm|cm|mSv|Sv|Gy|Bq|keV|MeV|분|초|시간)|"
    r"민감도|특이도|기준치|정상치|표준치|한도|계산식|공식)",
    re.IGNORECASE,
)
GENERIC_KEYWORDS = {
    "검사",
    "기기",
    "기술",
    "개념",
    "관련",
    "설명",
    "정의",
    "목적",
    "방법",
    "특성",
    "원리",
    "종류",
    "이해",
    "구분",
    "비교",
    "있다",
    "한다",
    "수",
    "및",
    "등",
    "대한",
    "위한",
    "각종",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize(value: Any) -> str:
    return str(value or "").strip()


def compact(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", normalize(value)).lower()


def text_for_alignment(row: dict[str, Any]) -> str:
    return " ".join(
        normalize(row.get(key))
        for key in ["source_excerpt_for_review", "source_file", "source_path"]
    ).lower()


def text_for_risk_scan(row: dict[str, Any]) -> str:
    return " ".join(
        normalize(row.get(key))
        for key in ["source_excerpt_for_review", "source_file"]
    ).lower()


def terms(value: Any) -> set[str]:
    raw_terms = re.findall(r"[0-9A-Za-z가-힣]+", normalize(value).lower())
    output: set[str] = set()
    for term in raw_terms:
        if len(term) < 2:
            continue
        if term in GENERIC_KEYWORDS:
            continue
        if term.isdigit():
            continue
        output.add(term)
    return output


def objective_terms(scope: dict[str, Any], objective: dict[str, Any]) -> set[str]:
    detail = normalize(scope.get("detail"))
    objective_text = normalize(objective.get("objective"))
    tokens = terms(f"{detail} {objective_text}")
    tokens.update(term.lower() for term in DETAIL_HINTS.get(detail, []) if len(term) >= 2)
    return tokens - GENERIC_KEYWORDS


def source_risk_reasons(row: dict[str, Any]) -> list[str]:
    text = text_for_risk_scan(row)
    reasons: list[str] = []
    if HARD_HOLD_PATTERN.search(text):
        reasons.append("source_law_formula_currentness_risk")
    if VISUAL_HOLD_PATTERN.search(text):
        reasons.append("source_visual_table_formula_risk")
    if NUMERIC_RISK_PATTERN.search(text):
        reasons.append("source_numeric_threshold_or_formula_risk")
    return sorted(dict.fromkeys(reasons))


def stable_id(prefix: str, payload: Any) -> str:
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


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


def detail_score(row: dict[str, Any], candidate: dict[str, Any]) -> float:
    detail = normalize(candidate.get("detail"))
    text = text_for_alignment(row)
    score = 0.0
    if detail and detail.lower() in text:
        score += 8.0
    for hint in DETAIL_HINTS.get(detail, []):
        if hint.lower() in text:
            score += 3.0
    objectives = candidate.get("learning_objective_candidates") or []
    if objectives:
        score += min(float(objectives[0].get("score") or 0), 12.0) * 0.15
    return score


def direct_evidence_quality(
    row: dict[str, Any],
    scope: dict[str, Any],
    objective: dict[str, Any],
    detail_assignment_score: float,
) -> dict[str, Any]:
    text = text_for_alignment(row)
    focus_terms = objective_terms(scope, objective)
    matched_focus_terms = sorted(term for term in focus_terms if term and term in text)
    detail_hints = [term.lower() for term in DETAIL_HINTS.get(normalize(scope.get("detail")), [])]
    matched_detail_hints = sorted(term for term in detail_hints if term and term in text)
    objective_score = float(objective.get("score") or 0)
    risks = source_risk_reasons(row)
    score = detail_assignment_score
    score += min(objective_score, 12.0) * 0.2
    score += min(len(matched_focus_terms), 4) * 2.0
    score += min(len(matched_detail_hints), 3) * 1.5
    score -= len(risks) * 5.0
    return {
        "score": round(score, 3),
        "detail_assignment_score": round(detail_assignment_score, 3),
        "learning_objective_score": objective_score,
        "focus_term_hits": len(matched_focus_terms),
        "detail_hint_hits": len(matched_detail_hints),
        "matched_focus_terms": matched_focus_terms[:8],
        "matched_detail_hints": matched_detail_hints[:8],
        "risk_reasons": risks,
    }


def evidence_quality_label(quality: dict[str, Any], args: argparse.Namespace) -> str:
    if quality.get("risk_reasons"):
        return "hold"
    if float(quality.get("detail_assignment_score") or 0) < args.min_detail_assignment_score:
        return "use_supporting"
    if float(quality.get("learning_objective_score") or 0) < args.min_objective_score:
        return "use_supporting"
    if int(quality.get("focus_term_hits") or 0) < args.min_focus_term_hits:
        return "use_supporting"
    if float(quality.get("score") or 0) < args.min_direct_evidence_score:
        return "use_supporting"
    return "use_direct"


def selected_detail_candidates(row: dict[str, Any], max_details: int) -> list[dict[str, Any]]:
    scored = [(detail_score(row, candidate), candidate) for candidate in row.get("detail_candidates") or []]
    scored.sort(key=lambda item: (-item[0], normalize(item[1].get("detail"))))
    selected = [(score, candidate) for score, candidate in scored if score >= 2.0][:max_details]
    if not selected and scored:
        selected = [scored[0]]
    output: list[dict[str, Any]] = []
    for score, candidate in selected:
        item = dict(candidate)
        item["detail_assignment_score"] = round(score, 3)
        output.append(item)
    return output


def objective_for(candidate: dict[str, Any]) -> dict[str, Any]:
    objective = ((candidate.get("learning_objective_candidates") or [{}])[0]) or {}
    objective_id = objective.get("learning_objective_id") or objective.get("objective_id")
    return {
        "learning_objective_id": objective_id or "",
        "objective_id": objective_id or "",
        "objective": objective.get("objective") or "",
        "level": objective.get("level") or "",
        "major_unit": objective.get("major_unit") or "",
        "unit": objective.get("unit") or "",
        "mapping_method": "expanded_folder_detail_top_objective",
        "score": objective.get("score"),
    }


def ensure_scope_id(scope: dict[str, Any]) -> str:
    return stable_id("folder_scope", {key: scope.get(key) or "" for key in ["period", "subject", "field", "area", "detail"]})


def hold_reasons(scope: dict[str, Any], objective: dict[str, Any]) -> list[str]:
    text = " ".join(str(scope.get(key) or "") for key in ["field", "area", "detail"])
    text += " " + str(objective.get("objective") or "")
    reasons: list[str] = []
    if HARD_HOLD_PATTERN.search(text):
        reasons.append("hard_hold_law_numeric_formula_or_currentness")
    if VISUAL_HOLD_PATTERN.search(text):
        reasons.append("visual_table_formula_dependency_risk")
    if scope.get("area") == "방사선관리" and re.search(r"(선량|단위|한도)", text):
        reasons.append("radiation_management_unit_or_limit_recheck")
    return reasons


def content_tag(scope: dict[str, Any], objective: dict[str, Any], group_no: int) -> str:
    detail = scope.get("detail") or scope.get("area") or "세부 개념"
    objective_text = str(objective.get("objective") or "")
    if objective_text:
        cleaned = re.sub(r"^\s*\d+[\).]?\s*", "", objective_text).strip()
        cleaned = cleaned[:42].rstrip()
        return f"{detail}: {cleaned}"
    return f"{detail}: 문항화 지식 단위 {group_no}"


def answerable_point(scope: dict[str, Any], objective: dict[str, Any]) -> str:
    tag = content_tag(scope, objective, 1)
    return f"{tag}에 대해 텍스트만으로 정답 하나를 판단할 수 있는 핵심 개념을 묻는다."


def forbidden_points(scope: dict[str, Any], reasons: list[str]) -> list[str]:
    base = [
        "원문 문장, 기존 문제, 보기, 해설을 그대로 사용하지 않는다.",
        "RAG 근거는 정답 근거 확인용으로만 사용한다.",
        "표·그림·도표·영상 제시를 전제로 하지 않는다.",
        "법규·최신 수치·공식·표 기반 기준은 확인 전 자동 생성하지 않는다.",
    ]
    if scope.get("area") == "방사선관리":
        base.extend(["선량 한도, 최신 법규, 수치 기준, 계산형 문항은 보류한다."])
    if reasons:
        base.extend(f"보류 위험: {reason}" for reason in reasons)
    return sorted(dict.fromkeys(base))


def distractor_points(scope: dict[str, Any]) -> list[str]:
    area = str(scope.get("area") or "")
    points = list(AREA_DISTRACTORS.get(area, []))
    if len(points) < 3:
        detail = scope.get("detail") or area or "해당 범위"
        points.extend(
            [
                f"{detail}의 핵심 개념을 인접 개념과 혼동",
                f"{detail}의 적용 맥락을 다른 절차와 혼동",
                f"{detail}의 예외를 일반 원칙처럼 오해",
            ]
        )
    return sorted(dict.fromkeys(points))[:5]


def ref_record(row: dict[str, Any]) -> dict[str, Any]:
    quality = row.get("_direct_evidence_quality") or {}
    return {
        "rag_input_id": row.get("rag_input_id"),
        "source_chunk_id": row.get("source_chunk_id"),
        "source_file": row.get("source_file"),
        "source_path": row.get("source_path"),
        "page_or_slide": row.get("page_or_slide"),
        "content_sha256": row.get("content_sha256"),
        "evidence_role": "use_direct",
        "direct_evidence_quality": quality,
    }


def pair_quality(pair: list[dict[str, Any]], scope: dict[str, Any], objective: dict[str, Any]) -> dict[str, Any]:
    qualities = [row.get("_direct_evidence_quality") or {} for row in pair]
    matched_sets = [set(quality.get("matched_focus_terms") or []) for quality in qualities]
    shared_focus_terms = sorted(set.intersection(*matched_sets)) if matched_sets else []
    risk_reasons = sorted({reason for quality in qualities for reason in quality.get("risk_reasons") or []})
    scores = [float(quality.get("score") or 0) for quality in qualities]
    focus_hits = [int(quality.get("focus_term_hits") or 0) for quality in qualities]
    detail_hits = [int(quality.get("detail_hint_hits") or 0) for quality in qualities]
    return {
        "score_min": round(min(scores), 3) if scores else 0,
        "score_avg": round(sum(scores) / len(scores), 3) if scores else 0,
        "focus_term_hits_min": min(focus_hits) if focus_hits else 0,
        "detail_hint_hits_min": min(detail_hits) if detail_hits else 0,
        "shared_focus_terms": shared_focus_terms[:8],
        "risk_reasons": risk_reasons,
        "objective_terms": sorted(objective_terms(scope, objective))[:12],
    }


def readiness_reasons(
    scope: dict[str, Any],
    objective: dict[str, Any],
    pair: list[dict[str, Any]],
    quality: dict[str, Any],
    args: argparse.Namespace,
) -> list[str]:
    reasons = hold_reasons(scope, objective)
    if len(pair) < 2:
        reasons.append("use_direct_refs_lt_2")
    if quality.get("risk_reasons"):
        reasons.extend(quality["risk_reasons"])
    if float(objective.get("score") or 0) < args.min_objective_score:
        reasons.append("objective_score_below_threshold")
    if float(quality.get("score_min") or 0) < args.min_direct_evidence_score:
        reasons.append("direct_evidence_score_below_threshold")
    if int(quality.get("focus_term_hits_min") or 0) < args.min_focus_term_hits:
        reasons.append("objective_focus_terms_not_found_in_each_ref")
    if args.require_shared_focus_term and not quality.get("shared_focus_terms"):
        reasons.append("direct_refs_do_not_share_objective_focus_term")
    if len(distractor_points(scope)) < 3:
        reasons.append("distractor_points_lt_3")
    return sorted(dict.fromkeys(reasons))


def chunks_to_objects(
    grouped_rows: list[dict[str, Any]],
    scope: dict[str, Any],
    objective: dict[str, Any],
    max_objects: int,
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    rows = sorted(
        grouped_rows,
        key=lambda row: (str(row.get("source_file") or ""), int(row.get("page_or_slide") or 0), str(row.get("rag_input_id") or "")),
    )
    for index in range(0, min(len(rows), max_objects * 2), 2):
        pair = rows[index : index + 2]
        if len(pair) < 2:
            break
        group_no = index // 2 + 1
        quality = pair_quality(pair, scope, objective)
        reasons = readiness_reasons(scope, objective, pair, quality, args)
        readiness = "hold" if reasons else "ready"
        payload = {
            "scope": scope,
            "objective": objective.get("objective_id"),
            "refs": [row.get("rag_input_id") for row in pair],
            "group_no": group_no,
            "quality": quality,
        }
        objects.append(
            {
                "knowledge_object_id": stable_id("ko2x", payload),
                "created_at": now_iso(),
                "version": "v2_expanded",
                "source_builder": "expanded_folder_safe_chunks",
                "scope": scope,
                "learning_objective": objective,
                "objective_link_confidence": "direct" if not reasons else "weak",
                "content_tag": content_tag(scope, objective, group_no),
                "answerable_point": answerable_point(scope, objective),
                "use_direct_refs": [ref_record(row) for row in pair],
                "use_supporting_refs": [],
                "distractor_points": distractor_points(scope),
                "forbidden_points": forbidden_points(scope, reasons),
                "direct_evidence_pair_quality": quality,
                "generation_readiness": readiness,
                "hold_reasons": reasons,
                "ready_policy": {
                    "min_detail_assignment_score": args.min_detail_assignment_score,
                    "min_objective_score": args.min_objective_score,
                    "min_focus_term_hits": args.min_focus_term_hits,
                    "min_direct_evidence_score": args.min_direct_evidence_score,
                    "require_shared_focus_term": args.require_shared_focus_term,
                },
                "source_text_included": False,
                "question_generation_performed": False,
            }
        )
    return objects


def build(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    rows = read_jsonl(args.safe_chunks)
    grouped: dict[tuple[str, str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    scope_by_key: dict[tuple[str, str, str, str, str, str], dict[str, Any]] = {}
    objective_by_key: dict[tuple[str, str, str, str, str, str], dict[str, Any]] = {}
    chunk_quality: list[dict[str, Any]] = []

    for row in rows:
        coarse = row.get("folder_authoritative_scope") or {}
        for detail_candidate in selected_detail_candidates(row, args.max_details_per_chunk):
            objective = objective_for(detail_candidate)
            if not objective.get("objective_id"):
                continue
            scope = {
                "period": coarse.get("period") or "",
                "subject": coarse.get("subject") or "",
                "field": coarse.get("field") or "",
                "area": coarse.get("area") or "",
                "detail": detail_candidate.get("detail") or "",
            }
            scope["scope_id"] = ensure_scope_id(scope)
            key = (*[scope[k] for k in ["period", "subject", "field", "area", "detail"]], objective["objective_id"])
            quality = direct_evidence_quality(
                row,
                scope,
                objective,
                float(detail_candidate.get("detail_assignment_score") or 0),
            )
            quality_label = evidence_quality_label(quality, args)
            row_for_group = dict(row)
            row_for_group["_direct_evidence_quality"] = quality
            if quality_label == "use_direct":
                grouped[key].append(row_for_group)
            scope_by_key[key] = scope
            objective_by_key[key] = objective
            chunk_quality.append(
                {
                    "rag_input_id": row.get("rag_input_id"),
                    "scope": scope,
                    "learning_objective_id": objective["objective_id"],
                    "quality_label": quality_label,
                    "detail_assignment_score": detail_candidate.get("detail_assignment_score"),
                    "direct_evidence_quality": quality,
                    "source_text_included": False,
                }
            )

    objects: list[dict[str, Any]] = []
    sorted_groups = sorted(
        grouped.items(),
        key=lambda item: (-len(item[1]), item[0][1], item[0][2], item[0][3], item[0][4], item[0][5]),
    )
    for key, group_rows in sorted_groups:
        if len(group_rows) < 2:
            continue
        if len(objects) >= args.target_ready_objects and sum(1 for obj in objects if obj["generation_readiness"] == "ready") >= args.target_ready_objects:
            break
        objects.extend(
            chunks_to_objects(
                group_rows,
                scope_by_key[key],
                objective_by_key[key],
                args.max_objects_per_group,
                args,
            )
        )
        if len(objects) >= args.max_objects:
            break

    ready_count = sum(1 for obj in objects if obj["generation_readiness"] == "ready")
    report = {
        "created_at": now_iso(),
        "inputs": {"safe_chunks": str(args.safe_chunks)},
        "outputs": {
            "knowledge_objects": str(args.output_dir / "knowledge_objects_v2_expanded.jsonl"),
            "chunk_quality": str(args.output_dir / "semantic_chunk_quality_expanded.jsonl"),
            "report_json": str(args.output_dir / "knowledge_objects_v2_expanded_report.json"),
            "report_md": str(args.output_dir / "knowledge_objects_v2_expanded_report.md"),
        },
        "counts": {
            "input_safe_chunks": len(rows),
            "grouped_scope_objectives": len(grouped),
            "knowledge_objects": len(objects),
            "ready": ready_count,
            "hold": sum(1 for obj in objects if obj["generation_readiness"] == "hold"),
            "by_area": dict(Counter((obj.get("scope") or {}).get("area") for obj in objects)),
            "chunk_quality_labels": dict(Counter(row.get("quality_label") for row in chunk_quality)),
            "hold_reasons": dict(Counter(reason for obj in objects for reason in obj.get("hold_reasons") or [])),
        },
        "policy": {
            "source_text_included": False,
            "question_generation_performed": False,
            "ready_requires_two_safe_text_refs": True,
            "ready_requires_objective_focus_hits": args.min_focus_term_hits,
            "ready_requires_direct_evidence_score_gte": args.min_direct_evidence_score,
            "ready_requires_objective_score_gte": args.min_objective_score,
            "ready_requires_shared_focus_term": args.require_shared_focus_term,
            "target_ready_objects": args.target_ready_objects,
        },
    }
    return objects, chunk_quality, report


def write_markdown(path: Path, report: dict[str, Any], objects: list[dict[str, Any]]) -> None:
    lines = ["# Expanded Knowledge Objects v2 Report", "", "## Summary", ""]
    for key, value in report["counts"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Ready Objects", "", "| object | scope | objective | refs | distractors |", "|---|---|---|---:|---:|"])
    for obj in [item for item in objects if item["generation_readiness"] == "ready"][:60]:
        scope = obj.get("scope") or {}
        scope_label = " / ".join(str(scope.get(key) or "") for key in ["period", "subject", "field", "area", "detail"] if scope.get(key))
        objective = (obj.get("learning_objective") or {}).get("objective") or ""
        lines.append(
            f"| {obj.get('knowledge_object_id')} | {scope_label} | {objective} | "
            f"{len(obj.get('use_direct_refs') or [])} | {len(obj.get('distractor_points') or [])} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--safe-chunks", type=Path, default=DEFAULT_SAFE_CHUNKS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--max-details-per-chunk", type=int, default=1)
    parser.add_argument("--max-objects-per-group", type=int, default=3)
    parser.add_argument("--target-ready-objects", type=int, default=30)
    parser.add_argument("--max-objects", type=int, default=40)
    parser.add_argument("--min-detail-assignment-score", type=float, default=5.0)
    parser.add_argument("--min-objective-score", type=float, default=10.0)
    parser.add_argument("--min-focus-term-hits", type=int, default=1)
    parser.add_argument("--min-direct-evidence-score", type=float, default=9.0)
    parser.add_argument("--allow-unshared-focus-term", dest="require_shared_focus_term", action="store_false")
    parser.set_defaults(require_shared_focus_term=True)
    args = parser.parse_args()
    objects, chunk_quality, report = build(args)
    write_jsonl(args.output_dir / "knowledge_objects_v2_expanded.jsonl", objects)
    write_jsonl(args.output_dir / "semantic_chunk_quality_expanded.jsonl", chunk_quality)
    write_json(args.output_dir / "knowledge_objects_v2_expanded_report.json", report)
    write_markdown(args.output_dir / "knowledge_objects_v2_expanded_report.md", report, objects)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
