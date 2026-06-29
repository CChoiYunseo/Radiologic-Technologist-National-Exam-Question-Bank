#!/usr/bin/env python3
"""Build stricter Knowledge Objects from semantic sub-chunks.

This promotes only semantic segments that directly match a detail/objective.
Knowledge Object outputs contain refs and quality metadata only. A separate RAG
input JSONL stores segment content for evidence retrieval.
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

from build_expanded_knowledge_objects_v2 import (
    AREA_DISTRACTORS,
    DETAIL_HINTS,
    forbidden_points,
    hold_reasons,
    now_iso,
    read_jsonl,
    stable_id,
    write_json,
    write_jsonl,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SAFE_REVIEW = (
    PROJECT_ROOT
    / "resources/generated/folder_authoritative_detail_review/folder_detail_review_safe_text_priority.jsonl"
)
DEFAULT_RAG_INPUT = (
    PROJECT_ROOT
    / "resources/extracted/rag_index_input_folder_authoritative/rag_index_input_folder_authoritative.jsonl"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources/generated/knowledge_objects_v2_semantic"
DEFAULT_RAG_OUTPUT_DIR = PROJECT_ROOT / "resources/extracted/rag_index_input_semantic_ko_v2"

LOCAL_DETAIL_HINTS: dict[str, list[str]] = {
    "공중보건총론": ["공중보건", "건강", "예방", "지역사회", "건강개념"],
    "환경보건": ["환경", "오염", "수질", "대기", "폐기물", "식품", "식중독", "자연독", "소음", "진동", "산업", "근로자"],
    "역학 및 질병관리": ["역학", "감염", "감염병", "질병", "전파", "유행", "예방"],
    "방사선관리의 개념": ["방사선관리", "방어", "피폭", "관리구역", "방사성", "선원", "취급"],
    "방사선모니터링": ["모니터링", "감시", "측정", "개인", "작업환경", "방사성동위원소"],
    "각종 방사선 검출기의 원리 및 특성": ["검출기", "계수관", "전리함", "섬광", "반도체", "중성자"],
    "방사선 계측기의 교정 및 계측치의 통계": ["교정", "계수", "통계", "표준편차", "중성자"],
    "초음파 물리": ["초음파", "주파수", "파장", "감쇠", "반사", "굴절", "압전", "효과"],
    "상복부 초음파 검사": ["상복부", "간", "담낭", "췌장", "비장", "콩팥", "신장"],
    "표재성장기 초음파검사": ["갑상샘", "유방", "표재", "고환"],
    "SPECT 및 PET 시스템": ["spect", "pet", "fdg", "ct", "동시계수", "단층"],
    "섬광카메라 및 핵의학검사 관련기기": ["섬광카메라", "감마카메라", "collimator", "검출기", "방사성", "동위원소"],
    "핵의학 기기의 성능평가": ["성능평가", "균일도", "분해능", "정도관리", "pet", "spect"],
    "상지": ["상지", "손", "손목", "팔꿈치", "어깨", "빗장뼈", "쇄골", "관절", "상완", "전완"],
    "하지": ["하지", "무릎", "발목", "발", "대퇴", "하퇴", "정강", "종아리"],
    "골반": ["골반", "엉덩", "고관절", "천골", "장골", "좌골", "치골"],
    "척추": ["척추", "목뼈", "경추", "흉추", "요추", "천추", "extension", "굴곡"],
    "두개부": ["두개", "머리", "안와", "부비동", "하악", "악관절"],
    "흉부": ["흉부", "폐", "심장", "갈비", "늑골", "흉골"],
    "복부": ["복부", "위", "장", "간", "신장", "요로"],
}

GENERIC_TERMS = {
    "검사",
    "기기",
    "기술",
    "개념",
    "관련",
    "설명",
    "설명할",
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
    "대한",
    "위한",
    "각종",
    "영상",
    "방사선",
    "대해",
    "소견",
    "검사방법",
    "검사목적",
    "정상",
    "대하여",
    "목적",
}

HARD_HOLD_PATTERN = re.compile(
    r"(법규|법률|조문|시행령|시행규칙|고시|선량한도|허용선량|"
    r"별표|제\s*\d+\s*조|최신\s*기준|질병관리청|식품의약품안전처|보건복지부)",
    re.IGNORECASE,
)
VISUAL_HOLD_PATTERN = re.compile(
    r"(\[?\s*(그림|표)\s*[0-9]+|도표|영상\s*(?:판독|해석|제시)|"
    r"\bfig\.?\s*[0-9]+|\btable\.?\s*[0-9]+)",
    re.IGNORECASE,
)
FORMULA_NUMERIC_HOLD_PATTERN = re.compile(
    r"(수식|공식|방정식|계산식|"
    r"\d+(?:\.\d+)?\s*(?:kV|mA|mAs|Gy|Sv|Bq|keV|MeV|MHz|mmHg|mGy|mSv|MBq|GBq)\b|"
    r"\d+\s*(?:%|mm|cm)|민감도|특이도|기준치|정상치|표준치|한도)",
    re.IGNORECASE,
)


def compact_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def normalize_token(value: str) -> str:
    token = re.sub(r"[^0-9A-Za-z가-힣]+", "", value.lower())
    for suffix in ["으로부터", "으로써", "에서", "으로", "에게", "부터", "까지", "에는", "으로", "의", "을", "를", "은", "는", "이", "가", "과", "와", "도", "만", "에", "로"]:
        if len(token) > len(suffix) and token.endswith(suffix):
            token = token[: -len(suffix)]
            break
    return token


def token_set(value: Any) -> set[str]:
    output: set[str] = set()
    for raw in re.findall(r"[0-9A-Za-z가-힣]+", str(value or "").lower()):
        token = normalize_token(raw)
        if len(token) < 2 or token.isdigit() or token in GENERIC_TERMS:
            continue
        output.add(token)
    return output


def objective_terms(scope: dict[str, Any], objective: dict[str, Any]) -> set[str]:
    text = objective.get("objective") or ""
    terms = token_set(text)
    detail_terms = token_set(scope.get("detail") or "")
    area_terms = token_set(scope.get("area") or "")
    # Objective focus must come from the objective itself. Detail hints are
    # evaluated separately so a broad detail word cannot prove objective match.
    specific_terms = terms - detail_terms - area_terms - GENERIC_TERMS
    return {term for term in specific_terms if len(term) >= 2}


def detail_hint_terms(detail: str) -> set[str]:
    hints = list(DETAIL_HINTS.get(detail or "", []))
    hints.extend(LOCAL_DETAIL_HINTS.get(detail or "", []))
    return {normalize_token(term) for term in hints if normalize_token(term)}


def scope_from_candidate(coarse: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    scope = {
        "period": coarse.get("period") or "",
        "subject": coarse.get("subject") or "",
        "field": coarse.get("field") or candidate.get("field") or "",
        "area": coarse.get("area") or candidate.get("area") or "",
        "detail": candidate.get("detail") or "",
    }
    scope["scope_id"] = stable_id(
        "folder_scope",
        {key: scope.get(key) or "" for key in ["period", "subject", "field", "area", "detail"]},
    )
    return scope


def objective_from_candidate(objective: dict[str, Any]) -> dict[str, Any]:
    objective_id = objective.get("learning_objective_id") or objective.get("objective_id")
    return {
        "learning_objective_id": objective_id or "",
        "objective_id": objective_id or "",
        "objective": objective.get("objective") or "",
        "level": objective.get("level") or "",
        "major_unit": objective.get("major_unit") or "",
        "unit": objective.get("unit") or "",
        "mapping_method": "semantic_segment_objective_match",
        "score": objective.get("score"),
    }


def split_sentences(content: str) -> list[str]:
    text = content.replace("\r", "\n")
    text = re.sub(r"\n{2,}", "\n", text)
    pieces: list[str] = []
    for line in text.splitlines():
        line = compact_text(line)
        if not line:
            continue
        parts = re.split(r"(?<=[.!?。！？])\s+|(?<=다\.)\s+|(?<=요\.)\s+", line)
        pieces.extend(compact_text(part) for part in parts if compact_text(part))
    return pieces


def semantic_segments(content: str, min_chars: int, max_chars: int) -> list[str]:
    sentences = split_sentences(content)
    segments: list[str] = []
    current: list[str] = []
    current_len = 0
    for sentence in sentences:
        if len(sentence) > max_chars:
            if current:
                segments.append(compact_text(" ".join(current)))
                current, current_len = [], 0
            for start in range(0, len(sentence), max_chars):
                part = sentence[start : start + max_chars]
                if len(part) >= min_chars:
                    segments.append(compact_text(part))
            continue
        if current and current_len + len(sentence) + 1 > max_chars:
            segment = compact_text(" ".join(current))
            if len(segment) >= min_chars:
                segments.append(segment)
            current, current_len = [], 0
        current.append(sentence)
        current_len += len(sentence) + 1
    if current:
        segment = compact_text(" ".join(current))
        if len(segment) >= min_chars:
            segments.append(segment)
    if not segments:
        text = compact_text(content)
        if len(text) >= min_chars:
            segments.append(text[:max_chars])
    return segments


def segment_risk_reasons(segment: str, scope: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if HARD_HOLD_PATTERN.search(segment):
        reasons.append("semantic_segment_law_or_currentness_risk")
    if VISUAL_HOLD_PATTERN.search(segment):
        reasons.append("semantic_segment_visual_table_risk")
    if FORMULA_NUMERIC_HOLD_PATTERN.search(segment):
        reasons.append("semantic_segment_numeric_formula_risk")
    if scope.get("area") == "방사선관리" and re.search(r"(선량|단위|한도|법규|법령)", segment):
        reasons.append("radiation_management_unit_limit_or_law_risk")
    return sorted(dict.fromkeys(reasons))


def local_ko_hold_reasons(scope: dict[str, Any], objective: dict[str, Any]) -> list[str]:
    text = f"{objective.get('objective') or ''} {scope.get('area') or ''} {scope.get('detail') or ''}"
    reasons: list[str] = []
    if scope.get("area") == "방사선관리" and re.search(r"(기술\s*기준|선량|한도|법규|법령|저장|운반)", text):
        reasons.append("radiation_management_technical_standard_or_law_hold")
    if re.search(r"(최신\s*기준|법규|법령|조문|시행령|시행규칙)", text):
        reasons.append("objective_law_or_currentness_hold")
    return sorted(dict.fromkeys(reasons))


def quality_for_segment(
    segment: str,
    scope: dict[str, Any],
    objective: dict[str, Any],
    detail_assignment_score: float,
) -> dict[str, Any]:
    segment_tokens = token_set(segment)
    focus_terms = objective_terms(scope, objective)
    detail_hints = detail_hint_terms(scope.get("detail") or "")
    matched_focus_terms = sorted(term for term in focus_terms if term in segment_tokens or term in segment)
    matched_detail_hints = sorted(term for term in detail_hints if term and (term in segment_tokens or term in segment))
    risks = segment_risk_reasons(segment, scope)
    objective_score = float(objective.get("score") or 0)
    score = 0.0
    score += min(objective_score, 13.0) * 0.25
    score += min(detail_assignment_score, 8.0) * 0.5
    score += min(len(matched_focus_terms), 4) * 3.0
    score += min(len(matched_detail_hints), 3) * 1.5
    score -= len(risks) * 6.0
    return {
        "score": round(score, 3),
        "detail_assignment_score": round(detail_assignment_score, 3),
        "learning_objective_score": objective_score,
        "focus_term_hits": len(matched_focus_terms),
        "detail_hint_hits": len(matched_detail_hints),
        "matched_focus_terms": matched_focus_terms[:8],
        "matched_detail_hints": matched_detail_hints[:8],
        "risk_reasons": risks,
        "segment_chars": len(segment),
    }


def direct_label(quality: dict[str, Any], args: argparse.Namespace) -> str:
    if quality.get("risk_reasons"):
        return "hold"
    if float(quality.get("learning_objective_score") or 0) < args.min_objective_score:
        return "use_supporting"
    if int(quality.get("focus_term_hits") or 0) < args.min_focus_term_hits:
        return "use_supporting"
    if int(quality.get("detail_hint_hits") or 0) < args.min_detail_hint_hits:
        return "use_supporting"
    if float(quality.get("score") or 0) < args.min_direct_evidence_score:
        return "use_supporting"
    return "use_direct"


def distractor_points(scope: dict[str, Any]) -> list[str]:
    points = list(AREA_DISTRACTORS.get(scope.get("area") or "", []))
    if len(points) < 3:
        detail = scope.get("detail") or scope.get("area") or "해당 범위"
        points.extend(
            [
                f"{detail}의 핵심 개념을 인접 개념과 혼동",
                f"{detail}의 적용 맥락을 다른 절차와 혼동",
                f"{detail}의 예외를 일반 원칙처럼 오해",
            ]
        )
    return sorted(dict.fromkeys(points))[:5]


def semantic_ref(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "rag_input_id": row["rag_input_id"],
        "source_chunk_id": row.get("source_chunk_id"),
        "parent_rag_input_id": row.get("parent_rag_input_id"),
        "parent_source_chunk_id": row.get("parent_source_chunk_id"),
        "source_file": row.get("source_file"),
        "source_path": row.get("source_path"),
        "page_or_slide": row.get("page_or_slide"),
        "content_sha256": row.get("content_sha256"),
        "semantic_segment_index": row.get("semantic_segment_index"),
        "evidence_role": "use_direct",
        "direct_evidence_quality": row.get("direct_evidence_quality") or {},
    }


def pair_quality(pair: list[dict[str, Any]], scope: dict[str, Any], objective: dict[str, Any]) -> dict[str, Any]:
    qualities = [row.get("direct_evidence_quality") or {} for row in pair]
    focus_sets = [set(quality.get("matched_focus_terms") or []) for quality in qualities]
    shared_focus_terms = sorted(set.intersection(*focus_sets)) if focus_sets else []
    parent_ids = [row.get("parent_rag_input_id") for row in pair]
    scores = [float(quality.get("score") or 0) for quality in qualities]
    return {
        "score_min": round(min(scores), 3) if scores else 0,
        "score_avg": round(sum(scores) / len(scores), 3) if scores else 0,
        "focus_term_hits_min": min(int(q.get("focus_term_hits") or 0) for q in qualities) if qualities else 0,
        "detail_hint_hits_min": min(int(q.get("detail_hint_hits") or 0) for q in qualities) if qualities else 0,
        "shared_focus_terms": shared_focus_terms[:8],
        "distinct_parent_refs": len(set(parent_ids)),
        "objective_terms": sorted(objective_terms(scope, objective))[:12],
    }


def content_tag(scope: dict[str, Any], objective: dict[str, Any], group_no: int) -> str:
    objective_text = re.sub(r"^\s*\d+[\).]?\s*", "", str(objective.get("objective") or "")).strip()
    if objective_text:
        return f"{scope.get('detail') or scope.get('area')}: {objective_text[:42].rstrip()}"
    return f"{scope.get('detail') or scope.get('area')}: semantic KO {group_no}"


def answerable_point(scope: dict[str, Any], objective: dict[str, Any], quality: dict[str, Any]) -> str:
    focus = ", ".join(quality.get("shared_focus_terms") or quality.get("objective_terms") or [])
    if focus:
        return f"{content_tag(scope, objective, 1)} 중 {focus}에 해당하는 텍스트 기반 핵심 개념을 묻는다."
    return f"{content_tag(scope, objective, 1)}에 해당하는 텍스트 기반 핵심 개념을 묻는다."


def make_semantic_rag_row(
    raw_row: dict[str, Any],
    review_row: dict[str, Any],
    segment: str,
    segment_index: int,
    scope: dict[str, Any],
    objective: dict[str, Any],
    quality: dict[str, Any],
) -> dict[str, Any]:
    content_hash = hashlib.sha256(segment.encode("utf-8")).hexdigest()
    parent_id = raw_row.get("rag_input_id") or review_row.get("rag_input_id")
    rag_input_id = stable_id(
        "rag_sem",
        {
            "parent": parent_id,
            "segment": segment_index,
            "scope_id": scope.get("scope_id"),
            "objective_id": objective.get("objective_id"),
            "content": content_hash,
        },
    )
    row = dict(raw_row)
    row.update(
        {
            "rag_input_id": rag_input_id,
            "parent_rag_input_id": parent_id,
            "parent_source_chunk_id": raw_row.get("source_chunk_id") or review_row.get("source_chunk_id"),
            "source_chunk_id": stable_id("semantic_chunk", {"parent": parent_id, "segment": segment_index, "content": content_hash}),
            "content": segment,
            "content_chars": len(segment),
            "content_sha256": content_hash,
            "chunk_type": "text",
            "approved_for_generation": False,
            "approved_for_rag_evidence": True,
            "candidate_rag_status": "ready_for_rag_evidence",
            "candidate_reasons": ["semantic_ko_direct_evidence_candidate"],
            "generation_review_status": "semantic_ko_v2_direct_candidate",
            "generation_hold_reasons": [],
            "needs_review": False,
            "mapped_period": scope.get("period"),
            "mapped_subject": scope.get("subject"),
            "mapped_field": scope.get("field"),
            "mapped_area": scope.get("area"),
            "mapped_detail": scope.get("detail"),
            "mapped_scope_id": scope.get("scope_id"),
            "scope_mapping_status": "semantic_ko_v2_detail_confirmed",
            "scope_mapping_confidence": "strict_semantic_segment",
            "scope_mapping_needs_review": False,
            "learning_objective_candidates": [objective],
            "learning_objective_id": objective.get("objective_id"),
            "learning_objective": objective.get("objective"),
            "semantic_segment_index": segment_index,
            "semantic_segment_builder": "build_semantic_knowledge_objects_v2",
            "direct_evidence_quality": quality,
            "rag_use_policy": "Evidence retrieval only. Do not copy source wording into generated questions, options, or explanations.",
            "copyright_use_policy": "Use as internal evidence only; generate all questions, options, and explanations in new wording.",
        }
    )
    return row


def build_knowledge_objects(
    direct_rows: list[dict[str, Any]],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in direct_rows:
        scope = row["semantic_scope"]
        objective = row["semantic_objective"]
        key = (
            scope.get("period") or "",
            scope.get("subject") or "",
            scope.get("field") or "",
            scope.get("area") or "",
            scope.get("detail") or "",
            objective.get("objective_id") or "",
        )
        grouped[key].append(row)

    objects: list[dict[str, Any]] = []
    objective_counts: Counter[str] = Counter()
    sorted_groups = sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))
    for _key, rows in sorted_groups:
        rows = sorted(
            rows,
            key=lambda row: (
                row.get("source_file") or "",
                int(row.get("page_or_slide") or 0),
                row.get("parent_rag_input_id") or "",
                row.get("semantic_segment_index") or 0,
            ),
        )
        used: set[str] = set()
        group_no = 0
        for first in rows:
            first_id = first.get("rag_input_id")
            if first_id in used:
                continue
            pair = [first]
            for second in rows:
                second_id = second.get("rag_input_id")
                if second_id == first_id or second_id in used:
                    continue
                if not args.allow_same_parent_pair and second.get("parent_rag_input_id") == first.get("parent_rag_input_id"):
                    continue
                pair.append(second)
                break
            if len(pair) < 2:
                continue
            scope = pair[0]["semantic_scope"]
            objective = pair[0]["semantic_objective"]
            objective_id = objective.get("objective_id") or ""
            if objective_counts[objective_id] >= args.max_objects_per_objective:
                continue
            quality = pair_quality(pair, scope, objective)
            reasons = hold_reasons(scope, objective)
            reasons.extend(local_ko_hold_reasons(scope, objective))
            if quality["focus_term_hits_min"] < args.min_focus_term_hits:
                reasons.append("objective_focus_terms_not_found_in_each_ref")
            if quality["detail_hint_hits_min"] < args.min_detail_hint_hits:
                reasons.append("detail_hint_terms_not_found_in_each_ref")
            if quality["score_min"] < args.min_direct_evidence_score:
                reasons.append("direct_evidence_score_below_threshold")
            if args.require_shared_focus_term and not quality["shared_focus_terms"]:
                reasons.append("direct_refs_do_not_share_objective_focus_term")
            if not args.allow_same_parent_pair and quality["distinct_parent_refs"] < 2:
                reasons.append("direct_refs_not_distinct_parent_chunks")
            if reasons:
                continue
            group_no += 1
            payload = {
                "scope": scope,
                "objective": objective.get("objective_id"),
                "refs": [row.get("rag_input_id") for row in pair],
                "quality": quality,
                "group_no": group_no,
            }
            objects.append(
                {
                    "knowledge_object_id": stable_id("ko2s", payload),
                    "created_at": now_iso(),
                    "version": "v2_semantic",
                    "source_builder": "semantic_segment_safe_chunks",
                    "scope": scope,
                    "learning_objective": objective,
                    "objective_link_confidence": "direct",
                    "content_tag": content_tag(scope, objective, group_no),
                    "answerable_point": answerable_point(scope, objective, quality),
                    "use_direct_refs": [semantic_ref(row) for row in pair],
                    "use_supporting_refs": [],
                    "distractor_points": distractor_points(scope),
                    "forbidden_points": forbidden_points(scope, []),
                    "direct_evidence_pair_quality": quality,
                    "generation_readiness": "ready",
                    "hold_reasons": [],
                    "ready_policy": {
                        "semantic_segmented": True,
                        "min_objective_score": args.min_objective_score,
                        "min_focus_term_hits": args.min_focus_term_hits,
                        "min_detail_hint_hits": args.min_detail_hint_hits,
                        "min_direct_evidence_score": args.min_direct_evidence_score,
                        "require_shared_focus_term": args.require_shared_focus_term,
                        "require_distinct_parent_refs": not args.allow_same_parent_pair,
                    },
                    "source_text_included": False,
                    "question_generation_performed": False,
                }
            )
            used.update(row["rag_input_id"] for row in pair)
            objective_counts[objective_id] += 1
            if group_no >= args.max_objects_per_group:
                break
        if len(objects) >= args.max_objects:
            break
    return objects


def build(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    review_rows = read_jsonl(args.safe_review)
    raw_rows = {row.get("rag_input_id"): row for row in read_jsonl(args.rag_input)}
    semantic_quality_rows: list[dict[str, Any]] = []
    semantic_rag_rows: list[dict[str, Any]] = []
    direct_rows: list[dict[str, Any]] = []
    seen_segment_scope_objective: set[tuple[str, str, str]] = set()

    for review_row in review_rows:
        parent_id = review_row.get("rag_input_id")
        raw_row = raw_rows.get(parent_id)
        if not raw_row or not raw_row.get("content"):
            continue
        segments = semantic_segments(raw_row.get("content") or "", args.min_segment_chars, args.max_segment_chars)
        coarse = review_row.get("folder_authoritative_scope") or {}
        for segment_index, segment in enumerate(segments):
            for detail_candidate in review_row.get("detail_candidates") or []:
                scope = scope_from_candidate(coarse, detail_candidate)
                detail_assignment_score = float(detail_candidate.get("detail_assignment_score") or 0)
                for objective_candidate in (detail_candidate.get("learning_objective_candidates") or [])[: args.max_objectives_per_detail]:
                    objective = objective_from_candidate(objective_candidate)
                    if not objective.get("objective_id"):
                        continue
                    quality = quality_for_segment(segment, scope, objective, detail_assignment_score)
                    label = direct_label(quality, args)
                    semantic_id_payload = {
                        "parent": parent_id,
                        "segment": segment_index,
                        "scope": scope,
                        "objective": objective.get("objective_id"),
                    }
                    semantic_candidate_id = stable_id("semcand", semantic_id_payload)
                    semantic_quality_rows.append(
                        {
                            "semantic_candidate_id": semantic_candidate_id,
                            "parent_rag_input_id": parent_id,
                            "source_chunk_id": review_row.get("source_chunk_id"),
                            "source_file": review_row.get("source_file"),
                            "page_or_slide": review_row.get("page_or_slide"),
                            "semantic_segment_index": segment_index,
                            "scope": scope,
                            "learning_objective_id": objective.get("objective_id"),
                            "quality_label": label,
                            "direct_evidence_quality": quality,
                            "source_text_included": False,
                        }
                    )
                    if label != "use_direct":
                        continue
                    dedupe_key = (parent_id, str(segment_index), objective.get("objective_id") or "")
                    if dedupe_key in seen_segment_scope_objective:
                        continue
                    seen_segment_scope_objective.add(dedupe_key)
                    semantic_row = make_semantic_rag_row(
                        raw_row,
                        review_row,
                        segment,
                        segment_index,
                        scope,
                        objective,
                        quality,
                    )
                    semantic_row["semantic_scope"] = scope
                    semantic_row["semantic_objective"] = objective
                    semantic_rag_rows.append({key: value for key, value in semantic_row.items() if key not in {"semantic_scope", "semantic_objective"}})
                    direct_rows.append(semantic_row)

    objects = build_knowledge_objects(direct_rows, args)
    object_ref_ids = {ref.get("rag_input_id") for obj in objects for ref in obj.get("use_direct_refs") or []}
    deduped_semantic_rag_rows: dict[str, dict[str, Any]] = {}
    for row in semantic_rag_rows:
        rag_input_id = row.get("rag_input_id")
        if rag_input_id in object_ref_ids and rag_input_id not in deduped_semantic_rag_rows:
            deduped_semantic_rag_rows[rag_input_id] = row
    semantic_rag_rows = list(deduped_semantic_rag_rows.values())

    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {"safe_review": str(args.safe_review), "rag_input": str(args.rag_input)},
        "outputs": {
            "knowledge_objects": str(args.output_dir / "knowledge_objects_v2_semantic.jsonl"),
            "semantic_chunk_quality": str(args.output_dir / "semantic_chunk_quality_v2.jsonl"),
            "semantic_rag_input": str(args.rag_output_dir / "rag_index_input_semantic_ko_v2.jsonl"),
            "report_json": str(args.output_dir / "knowledge_objects_v2_semantic_report.json"),
            "report_md": str(args.output_dir / "knowledge_objects_v2_semantic_report.md"),
        },
        "counts": {
            "safe_review_rows": len(review_rows),
            "raw_rows_joined": sum(1 for row in review_rows if row.get("rag_input_id") in raw_rows),
            "semantic_quality_rows": len(semantic_quality_rows),
            "semantic_rag_rows_for_ready_objects": len(semantic_rag_rows),
            "direct_segment_candidates": len(direct_rows),
            "knowledge_objects": len(objects),
            "ready": len(objects),
            "by_area": dict(Counter((obj.get("scope") or {}).get("area") for obj in objects)),
            "quality_labels": dict(Counter(row.get("quality_label") for row in semantic_quality_rows)),
            "risk_reasons": dict(
                Counter(
                    reason
                    for row in semantic_quality_rows
                    for reason in (row.get("direct_evidence_quality") or {}).get("risk_reasons", [])
                )
            ),
        },
        "policy": {
            "source_text_in_ko_outputs": False,
            "semantic_rag_input_contains_content_for_evidence_index": True,
            "question_generation_performed": False,
            "ready_requires_direct_segments_gte_2": True,
            "ready_requires_distinct_parent_refs": not args.allow_same_parent_pair,
            "ready_requires_shared_focus_term": args.require_shared_focus_term,
            "text_only_generation_scope": "1_2_period_only",
        },
    }
    return objects, semantic_quality_rows, semantic_rag_rows, report


def write_markdown(path: Path, report: dict[str, Any], objects: list[dict[str, Any]]) -> None:
    lines = ["# Semantic Knowledge Objects v2 Report", "", "## Summary", ""]
    for key, value in report["counts"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Ready Objects", "", "| object | scope | objective | refs | shared focus |", "|---|---|---|---:|---|"])
    for obj in objects[:80]:
        scope = obj.get("scope") or {}
        scope_label = " / ".join(str(scope.get(key) or "") for key in ["period", "subject", "field", "area", "detail"] if scope.get(key))
        objective = (obj.get("learning_objective") or {}).get("objective") or ""
        shared = ", ".join((obj.get("direct_evidence_pair_quality") or {}).get("shared_focus_terms") or [])
        lines.append(f"| {obj.get('knowledge_object_id')} | {scope_label} | {objective} | {len(obj.get('use_direct_refs') or [])} | {shared} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--safe-review", type=Path, default=DEFAULT_SAFE_REVIEW)
    parser.add_argument("--rag-input", type=Path, default=DEFAULT_RAG_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--rag-output-dir", type=Path, default=DEFAULT_RAG_OUTPUT_DIR)
    parser.add_argument("--min-segment-chars", type=int, default=120)
    parser.add_argument("--max-segment-chars", type=int, default=420)
    parser.add_argument("--max-objectives-per-detail", type=int, default=5)
    parser.add_argument("--max-objects-per-group", type=int, default=4)
    parser.add_argument("--max-objects-per-objective", type=int, default=2)
    parser.add_argument("--max-objects", type=int, default=40)
    parser.add_argument("--min-objective-score", type=float, default=10.0)
    parser.add_argument("--min-focus-term-hits", type=int, default=1)
    parser.add_argument("--min-detail-hint-hits", type=int, default=1)
    parser.add_argument("--min-direct-evidence-score", type=float, default=9.0)
    parser.add_argument("--allow-unshared-focus-term", dest="require_shared_focus_term", action="store_false")
    parser.add_argument("--allow-same-parent-pair", action="store_true")
    parser.set_defaults(require_shared_focus_term=True)
    args = parser.parse_args()

    objects, semantic_quality_rows, semantic_rag_rows, report = build(args)
    write_jsonl(args.output_dir / "knowledge_objects_v2_semantic.jsonl", objects)
    write_jsonl(args.output_dir / "semantic_chunk_quality_v2.jsonl", semantic_quality_rows)
    write_json(args.output_dir / "knowledge_objects_v2_semantic_report.json", report)
    write_markdown(args.output_dir / "knowledge_objects_v2_semantic_report.md", report, objects)
    write_jsonl(args.rag_output_dir / "rag_index_input_semantic_ko_v2.jsonl", semantic_rag_rows)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
