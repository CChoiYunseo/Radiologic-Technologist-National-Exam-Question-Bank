#!/usr/bin/env python3
"""Build conservative Knowledge Objects and Safe Generation Package v2 drafts.

The output is intentionally metadata-first. It does not emit OCR/textbook body
content. Any future question generator must use these objects as planning
records and still retrieve evidence separately for answer grounding.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKLIST = PROJECT_ROOT / "resources" / "generated" / "knowledge_object_planning" / "knowledge_object_rebuild_worklist.jsonl"
DEFAULT_MAPPED_RAG = PROJECT_ROOT / "resources" / "extracted" / "rag_index_input" / "rag_index_input_mapped.jsonl"
DEFAULT_GENERATION_SAFE_VECTOR_DB = PROJECT_ROOT / "resources" / "vector_db" / "subject_references_generation_safe"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources" / "generated" / "knowledge_objects_v1"


LAW_OR_CURRENTNESS_PATTERN = re.compile(r"(법규|법률|의료법|조문|시행령|시행규칙|고시|선량한도|허가|신고)")
NUMERIC_FORMULA_PATTERN = re.compile(r"(수치|단위|공식|계산|선량|관전압|관전류|kV|mA|mGy|Sv)")
VISUAL_PATTERN = re.compile(r"(표|그림|도표|수식|회로|스펙트럼)")
CONFLICT_TERMS = [
    "소아",
    "노인",
    "아동",
    "이물질",
    "골반",
    "엉덩관절",
    "척추",
    "흉부",
    "복부",
    "유방",
    "하지",
    "상지",
    "두개",
    "심장",
    "관상동맥",
    "비뇨",
    "생식기",
    "산부인과",
    "상복부",
    "표재성장기",
]


MISCONCEPTION_PRESETS: dict[str, list[str]] = {
    "전산화단층검사": [
        "장치 구성 요소와 영상 재구성 단계를 혼동",
        "영상 후처리와 원자료 재구성을 혼동",
        "화질 인자와 선량 인자를 단일 개념으로 혼동",
    ],
    "초음파기술": [
        "주파수, 파장, 투과심도의 관계를 혼동",
        "반사, 굴절, 산란, 감쇠를 같은 현상으로 혼동",
        "도플러 원리와 일반 B-mode 영상 원리를 혼동",
    ],
    "방사선생물": [
        "세포 수준 영향과 조직 수준 영향을 혼동",
        "급성 영향과 만발성 영향을 혼동",
        "직접작용과 간접작용을 혼동",
    ],
    "방사선영상": [
        "검사 자세와 투사 방향을 혼동",
        "영상 평가 요소와 촬영 조건을 혼동",
        "부위별 촬영 목적과 해부학적 기준점을 혼동",
    ],
    "투시조영검사": [
        "검사 목적과 조영제 선택 기준을 혼동",
        "투시 절차와 일반 촬영 절차를 혼동",
        "조영제 사용 전 확인 사항과 검사 후 관찰 사항을 혼동",
    ],
    "심맥관 및 중재술": [
        "진단 혈관조영과 중재 시술의 목적을 혼동",
        "카테터, 가이드와이어, 조영제의 역할을 혼동",
        "혈관 부위별 접근 원칙을 혼동",
    ],
    "방사성의약품": [
        "방사성의약품의 물리적 특성과 생물학적 집적 기전을 혼동",
        "표지 과정과 정도관리 과정을 혼동",
        "검사 목적과 핵종 선택 기준을 혼동",
    ],
    "공중보건": [
        "개인 건강관리와 지역사회 보건 개념을 혼동",
        "역학 지표와 질병관리 절차를 혼동",
        "환경보건 요인과 감염관리 요인을 혼동",
    ],
    "방사선관리": [
        "방사선 방어 원칙과 행정 절차를 혼동",
        "개인 모니터링과 작업환경 모니터링을 혼동",
        "관리구역 개념과 선량한도 개념을 혼동",
    ],
    "방사선계측": [
        "검출기 원리와 선량 단위를 혼동",
        "계측기 교정과 측정값 통계를 혼동",
        "조사선량과 흡수선량을 혼동",
    ],
    "의료영상정보": [
        "영상 생성 과정과 영상 평가 지표를 혼동",
        "디지털 영상 저장과 영상 처리 과정을 혼동",
        "기록계 특성과 검출기 특성을 혼동",
    ],
}


QUESTION_TYPE_PRESETS: dict[str, list[str]] = {
    "definition": ["개념형"],
    "mechanism": ["개념형", "원리이해형"],
    "comparison": ["비교형", "개념형"],
    "procedure": ["상황판단형", "절차이해형"],
    "quality_factor": ["개념형", "비교형"],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
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


def stable_id(prefix: str, payload: Any) -> str:
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def scope_text(scope: dict[str, Any]) -> str:
    return " ".join(str(scope.get(key) or "") for key in ["subject", "field", "area", "detail"]).strip()


def load_generation_safe_ids(db_dir: Path) -> set[str]:
    db_path = db_dir / "chunks.sqlite"
    if not db_path.exists():
        return set()
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT chunk_id, metadata_json FROM chunks").fetchall()
    finally:
        conn.close()
    ids: set[str] = set()
    for chunk_id, metadata_json in rows:
        rag_input_id = ""
        if metadata_json:
            try:
                metadata = json.loads(metadata_json)
                rag_input_id = str(metadata.get("rag_input_id") or "")
            except json.JSONDecodeError:
                rag_input_id = ""
        ids.add(rag_input_id or str(chunk_id))
    return {item for item in ids if item}


def index_mapped_rows(path: Path) -> dict[str, dict[str, Any]]:
    rows = read_jsonl(path)
    return {str(row.get("rag_input_id")): row for row in rows if row.get("rag_input_id")}


def top_learning_objectives(chunk_rows: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    scores: Counter[str] = Counter()
    for row in chunk_rows:
        for objective in row.get("learning_objective_candidates") or []:
            objective_id = str(objective.get("objective_id") or objective.get("learning_objective_id") or "")
            if not objective_id:
                continue
            by_id.setdefault(
                objective_id,
                {
                    "objective_id": objective_id,
                    "objective": objective.get("objective"),
                    "level": objective.get("level"),
                    "major_unit": objective.get("major_unit"),
                    "unit": objective.get("unit"),
                    "keywords": objective.get("keywords") or [],
                    "confidence": "candidate",
                },
            )
            try:
                score = float(objective.get("score") or 1)
            except (TypeError, ValueError):
                score = 1.0
            scores[objective_id] += max(1, int(score * 10))
    selected = [by_id[obj_id] for obj_id, _ in scores.most_common(limit)]
    for item in selected:
        count_score = scores[item["objective_id"]]
        item["confidence"] = "high" if count_score >= 80 else "medium" if count_score >= 30 else "low"
    return selected


def concept_type_for(scope: dict[str, Any], objectives: list[dict[str, Any]]) -> str:
    text = scope_text(scope) + " " + " ".join(str(obj.get("objective") or "") for obj in objectives)
    if re.search(r"(절차|검사|시술|관리|사용|운영)", text):
        return "procedure"
    if re.search(r"(차이|비교|종류|분류|특성)", text):
        return "comparison"
    if re.search(r"(원리|기전|발생|작용|재구성)", text):
        return "mechanism"
    if re.search(r"(화질|정도관리|성능|평가|선량)", text):
        return "quality_factor"
    return "definition"


def objective_scope_mismatch(scope: dict[str, Any], objectives: list[dict[str, Any]]) -> bool:
    scope_value = scope_text(scope)
    detail = str(scope.get("detail") or "")
    if not objectives:
        return False
    for objective in objectives[:3]:
        objective_text = str(objective.get("objective") or "")
        unit_text = str(objective.get("unit") or "")
        conflict_hits = [term for term in CONFLICT_TERMS if term in objective_text and term not in scope_value]
        if conflict_hits:
            return True
        if detail and unit_text and detail not in unit_text and unit_text not in detail:
            if detail not in objective_text and objective_text:
                return True
    return False


def hold_flags(
    scope: dict[str, Any],
    selected_chunks: list[dict[str, Any]],
    chunk_rows: list[dict[str, Any]],
    objectives: list[dict[str, Any]],
) -> list[str]:
    text = scope_text(scope)
    holds: set[str] = set()
    if not scope.get("detail"):
        holds.add("empty_detail")
    if LAW_OR_CURRENTNESS_PATTERN.search(text):
        holds.add("law_or_currentness")
    if NUMERIC_FORMULA_PATTERN.search(text):
        holds.add("numeric_or_formula_review")
    if VISUAL_PATTERN.search(text):
        holds.add("visual_or_table_formula_review")
    if selected_chunks:
        needs_review = sum(1 for chunk in selected_chunks if chunk.get("scope_mapping_needs_review"))
        area_only = sum(1 for chunk in selected_chunks if chunk.get("scope_mapping_confidence") == "area_only")
        if needs_review or area_only > len(selected_chunks) // 2:
            holds.add("scope_uncertain")
    if any(row.get("generation_hold_reasons") for row in chunk_rows):
        holds.add("source_marked_hold_review")
    if objective_scope_mismatch(scope, objectives):
        holds.add("learning_objective_scope_mismatch")
    return sorted(holds)


def ko_status(holds: list[str], has_safe_chunk: bool, lo_candidates: list[dict[str, Any]]) -> str:
    if "empty_detail" in holds or "law_or_currentness" in holds or "learning_objective_scope_mismatch" in holds:
        return "hold"
    if "scope_uncertain" in holds and not lo_candidates:
        return "hold"
    if has_safe_chunk and lo_candidates:
        return "reviewable"
    if lo_candidates:
        return "needs_generation_safety_review"
    return "needs_scope_and_objective_review"


def package_status(object_status: str, holds: list[str], chunk_refs: list[dict[str, Any]]) -> str:
    if object_status == "hold":
        return "hold"
    if object_status == "needs_generation_safety_review":
        return "needs_generation_safety_review"
    if object_status == "needs_scope_and_objective_review":
        return "needs_scope_and_objective_review"
    if "source_marked_hold_review" in holds or "numeric_or_formula_review" in holds or "visual_or_table_formula_review" in holds:
        return "reviewable"
    if sum(1 for ref in chunk_refs if ref.get("is_generation_safe_candidate")) >= 2:
        return "reviewable"
    return "hold"


def misconception_candidates(scope: dict[str, Any]) -> list[str]:
    area = str(scope.get("area") or "")
    detail = str(scope.get("detail") or "")
    base = MISCONCEPTION_PRESETS.get(area, [])
    if detail and detail != area:
        base = [f"{detail}의 핵심 개념을 인접 세부영역 개념과 혼동"] + base
    return base[:4]


def build_summary(scope: dict[str, Any], objectives: list[dict[str, Any]], concept_type: str) -> str:
    detail = scope.get("detail") or scope.get("area") or "해당 범위"
    if objectives:
        objective = objectives[0].get("objective") or "관련 학습목표"
        return f"{detail} 범위에서 {objective}와 연결되는 개념 근거 묶음이다."
    type_label = {
        "definition": "기본 개념",
        "mechanism": "작동 원리",
        "comparison": "비교 기준",
        "procedure": "검사 또는 관리 절차",
        "quality_factor": "품질 및 평가 요소",
    }.get(concept_type, "기본 개념")
    return f"{detail} 범위의 {type_label}을 문제 생성 전 검토하기 위한 근거 묶음이다."


def answerable_points(scope: dict[str, Any], objectives: list[dict[str, Any]], concept_type: str) -> list[str]:
    detail = scope.get("detail") or scope.get("area") or "해당 범위"
    points = []
    if objectives:
        for objective in objectives[:3]:
            points.append(f"{detail}에서 '{objective.get('objective')}' 학습목표에 맞는 정답 근거를 구성할 수 있다.")
    else:
        points.append(f"{detail}의 핵심 정의, 원리, 적용 상황을 구분하는 문항 후보를 구성할 수 있다.")
    if concept_type == "comparison":
        points.append(f"{detail} 안에서 서로 가까운 개념의 차이를 묻는 비교형 문항 후보를 구성할 수 있다.")
    elif concept_type == "procedure":
        points.append(f"{detail} 관련 검사 또는 관리 흐름에서 올바른 판단을 묻는 문항 후보를 구성할 수 있다.")
    return points[:4]


def build_objects(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    worklist = read_jsonl(args.worklist)
    mapped_index = index_mapped_rows(args.mapped_rag)
    safe_ids = load_generation_safe_ids(args.generation_safe_vector_db)

    knowledge_objects: list[dict[str, Any]] = []
    packages: list[dict[str, Any]] = []

    for item in worklist:
        scope = item.get("scope") or {}
        selected_chunks = item.get("selected_chunks") or []
        selected_ids = [str(chunk.get("rag_input_id")) for chunk in selected_chunks if chunk.get("rag_input_id")]
        chunk_rows = [mapped_index[rag_id] for rag_id in selected_ids if rag_id in mapped_index]
        objectives = top_learning_objectives(chunk_rows)
        concept_type = concept_type_for(scope, objectives)
        holds = hold_flags(scope, selected_chunks, chunk_rows, objectives)
        chunk_refs = []
        pages = []
        source_files = Counter()
        for chunk in selected_chunks:
            rag_id = str(chunk.get("rag_input_id") or "")
            page = chunk.get("page_or_slide")
            if page is not None:
                pages.append(page)
            if chunk.get("source_file"):
                source_files[str(chunk.get("source_file"))] += 1
            chunk_refs.append(
                {
                    "rag_input_id": rag_id,
                    "source_chunk_id": chunk.get("source_chunk_id"),
                    "source_file": chunk.get("source_file"),
                    "source_path": chunk.get("source_path"),
                    "page_or_slide": page,
                    "content_sha256": chunk.get("content_sha256"),
                    "scope_mapping_confidence": chunk.get("scope_mapping_confidence"),
                    "scope_mapping_status": chunk.get("scope_mapping_status"),
                    "scope_mapping_needs_review": chunk.get("scope_mapping_needs_review"),
                    "is_generation_safe_candidate": rag_id in safe_ids,
                }
            )
        page_range = [min(pages), max(pages)] if pages else []
        object_payload = {
            "scope": scope,
            "work_order": item.get("work_order"),
            "selected_ids": selected_ids,
            "concept_type": concept_type,
        }
        object_id = stable_id("ko", object_payload)
        status = ko_status(holds, any(ref["is_generation_safe_candidate"] for ref in chunk_refs), objectives)
        knowledge_object = {
            "object_id": object_id,
            "created_at": now_iso(),
            "version": "v1",
            "source_work_order": item.get("work_order"),
            "recommended_action": item.get("recommended_action"),
            "scope": scope,
            "concept_name": scope.get("detail") or scope.get("area"),
            "concept_type": concept_type,
            "summary": build_summary(scope, objectives, concept_type),
            "source_files": dict(source_files.most_common()),
            "page_range": page_range,
            "source_chunk_refs": chunk_refs,
            "source_chunk_count": len(chunk_refs),
            "learning_objective_candidates": objectives,
            "generation_flags": {
                "may_generate_text_question": status == "reviewable" and not holds,
                "requires_professional_review": True,
                "holds": holds,
                "status": status,
            },
            "evidence_policy": "answer_grounding_only_no_source_copy",
            "copyright_policy": "do_not_copy_source_sentences_generate_new_wording",
            "source_text_included": False,
        }
        knowledge_objects.append(knowledge_object)

        package_payload = {"object_id": object_id, "scope": scope, "object_status": status}
        sgp_status = package_status(status, holds, chunk_refs)
        packages.append(
            {
                "package_id": stable_id("sgp2", package_payload),
                "created_at": now_iso(),
                "version": "v2_draft",
                "status": sgp_status,
                "knowledge_object_ids": [object_id],
                "scope": scope,
                "learning_objective": objectives[0] if objectives else None,
                "allowed_question_types": QUESTION_TYPE_PRESETS.get(concept_type, ["개념형"]),
                "difficulty_candidates": ["하", "중"] if sgp_status != "hold" else [],
                "answerable_points": answerable_points(scope, objectives, concept_type),
                "misconception_candidates": misconception_candidates(scope),
                "forbidden_points": [
                    "원문 문장, 기존 문제, 보기, 해설을 그대로 사용하지 않는다.",
                    "근거 검색은 정답 검증용으로만 사용한다.",
                    "법규·최신 수치·공식·표·그림 단독 근거는 승인 전 자동 생성하지 않는다.",
                ],
                "hold_reasons": holds,
                "source_chunk_refs": chunk_refs,
                "source_text_included": False,
                "question_generation_performed": False,
            }
        )

    report = {
        "created_at": now_iso(),
        "inputs": {
            "worklist": str(args.worklist),
            "mapped_rag": str(args.mapped_rag),
            "generation_safe_vector_db": str(args.generation_safe_vector_db),
        },
        "outputs": {
            "knowledge_objects_jsonl": str(args.output_dir / "knowledge_objects_v1.jsonl"),
            "safe_generation_packages_jsonl": str(args.output_dir / "safe_generation_packages_v2_draft.jsonl"),
            "report_json": str(args.output_dir / "knowledge_objects_v1_report.json"),
            "report_md": str(args.output_dir / "knowledge_objects_v1_report.md"),
        },
        "counts": {
            "knowledge_objects": len(knowledge_objects),
            "safe_generation_packages_v2_draft": len(packages),
            "knowledge_object_status": dict(Counter(obj["generation_flags"]["status"] for obj in knowledge_objects)),
            "package_status": dict(Counter(pkg["status"] for pkg in packages)),
            "concept_types": dict(Counter(obj["concept_type"] for obj in knowledge_objects)),
            "objects_with_learning_objective": sum(1 for obj in knowledge_objects if obj["learning_objective_candidates"]),
            "objects_without_source_text": sum(1 for obj in knowledge_objects if obj["source_text_included"] is False),
        },
        "policy": {
            "source_text_included": False,
            "question_generation_performed": False,
            "strict_auto_promotion": False,
        },
    }
    return knowledge_objects, packages, report


def write_markdown(path: Path, report: dict[str, Any], objects: list[dict[str, Any]], packages: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Knowledge Objects v1 Report",
        "",
        "이 산출물은 원문을 포함하지 않는 문제 생성 전 구조화 자료이다.",
        "",
        "## Summary",
        "",
    ]
    for key, value in report["counts"].items():
        lines.append(f"- {key}: {value}")
    lines.extend([
        "",
        "## Package Status",
        "",
    ])
    for status, count in sorted(report["counts"]["package_status"].items()):
        lines.append(f"- {status}: {count}")
    lines.extend([
        "",
        "## Top Package Drafts",
        "",
        "| package_id | status | subject | field | area | detail | learning objective | chunks | holds |",
        "|---|---|---|---|---|---|---|---:|---|",
    ])
    object_by_id = {obj["object_id"]: obj for obj in objects}
    for pkg in packages[:40]:
        obj = object_by_id[pkg["knowledge_object_ids"][0]]
        scope = pkg["scope"]
        objective = (pkg.get("learning_objective") or {}).get("objective") or ""
        lines.append(
            "| {package_id} | {status} | {subject} | {field} | {area} | {detail} | {objective} | {chunks} | {holds} |".format(
                package_id=pkg["package_id"],
                status=pkg["status"],
                subject=scope.get("subject") or "",
                field=scope.get("field") or "",
                area=scope.get("area") or "",
                detail=scope.get("detail") or "",
                objective=objective,
                chunks=obj["source_chunk_count"],
                holds=", ".join(pkg.get("hold_reasons") or []),
            )
        )
    lines.extend([
        "",
        "## Next Step",
        "",
        "1. `reviewable` 패키지부터 원문을 직접 복사하지 않는 새 문장 요약을 보강한다.",
        "2. 학습목표 연결이 약하거나 보류 사유가 있는 패키지는 strict로 승격하지 않는다.",
        "3. strict 승격 후 세부영역당 최대 5문항 파일럿 생성만 수행한다.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worklist", type=Path, default=DEFAULT_WORKLIST)
    parser.add_argument("--mapped-rag", type=Path, default=DEFAULT_MAPPED_RAG)
    parser.add_argument("--generation-safe-vector-db", type=Path, default=DEFAULT_GENERATION_SAFE_VECTOR_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    objects, packages, report = build_objects(args)
    write_jsonl(args.output_dir / "knowledge_objects_v1.jsonl", objects)
    write_jsonl(args.output_dir / "safe_generation_packages_v2_draft.jsonl", packages)
    write_json(args.output_dir / "knowledge_objects_v1_report.json", report)
    write_markdown(args.output_dir / "knowledge_objects_v1_report.md", report, objects, packages)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
