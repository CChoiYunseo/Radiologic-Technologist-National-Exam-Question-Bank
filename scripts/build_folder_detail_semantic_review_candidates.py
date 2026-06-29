#!/usr/bin/env python3
"""Build semantic-review candidates from folder-authoritative detail reviews.

Inputs are detail review chunks whose coarse scope is fixed by the material
folder. This script groups safe text refs by folder area and candidate detail,
then prepares package candidates for Codex/LLM semantic review. It does not
approve generation or create questions.
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
DEFAULT_DETAIL_REVIEW = (
    PROJECT_ROOT
    / "resources"
    / "generated"
    / "folder_authoritative_detail_review"
    / "folder_detail_review_safe_text_priority.jsonl"
)
DEFAULT_OUTPUT_DIR = (
    PROJECT_ROOT / "resources" / "generated" / "folder_detail_semantic_review_candidates"
)
DETAIL_HINTS: dict[str, list[str]] = {
    "두개부": ["두개", "머리", "두부", "skull", "head"],
    "척추": ["척추", "경추", "흉추", "요추", "천추", "spine"],
    "흉부": ["흉부", "가슴", "폐", "늑골", "흉곽", "chest"],
    "복부": ["복부", "배", "위", "장", "복강", "abdomen"],
    "골반": ["골반", "고관절", "천장관절", "pelvis"],
    "상지": ["상지", "손", "손목", "어깨", "팔", "팔꿈치", "상완", "전완", "견관절"],
    "하지": ["하지", "발", "발목", "무릎", "대퇴", "하퇴", "슬관절", "족관절"],
    "영상치의학검사": ["치아", "치과", "치의학", "악관절", "파노라마"],
    "공중보건총론": ["공중보건", "건강", "보건", "예방"],
    "역학 및 감염병관리": ["역학", "감염", "감염병", "전파", "유행"],
    "환경보건": ["환경", "수질", "대기", "폐기물", "식품", "산업"],
    "방사선측정": ["측정", "선량", "계측", "검출", "전리함"],
    "방사선계측기": ["계측기", "검출기", "섬광", "반도체", "전리함"],
    "방사선 관리": ["관리", "방호", "차폐", "오염", "피폭"],
    "핵의학 검사장치": ["감마카메라", "spect", "pet", "스캐너", "검출기"],
    "방사성의약품": ["방사성의약품", "표지", "집적", "핵종", "순도"],
    "시료계측": ["시료", "계측", "검체", "계수"],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def short_hash(value: Any, length: int = 16) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
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


def best_objective(detail_candidate: dict[str, Any]) -> dict[str, Any]:
    objectives = detail_candidate.get("learning_objective_candidates") or []
    if not objectives:
        return {}
    return dict(objectives[0])


def ref_from_review(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "rag_input_id": row.get("rag_input_id"),
        "source_chunk_id": row.get("source_chunk_id"),
        "source_file": row.get("source_file"),
        "source_path": row.get("source_path"),
        "page_or_slide": row.get("page_or_slide"),
        "content_sha256": row.get("content_sha256"),
        "scope_mapping_status": "folder_authoritative_area_only",
        "scope_mapping_confidence": "folder_area_only",
        "scope_mapping_needs_review": True,
        "review_package_id": row.get("review_package_id"),
    }


def source_diversity_score(refs: list[dict[str, Any]]) -> tuple[int, int]:
    files = {ref.get("source_file") for ref in refs if ref.get("source_file")}
    pages = {f"{ref.get('source_file')}:{ref.get('page_or_slide')}" for ref in refs}
    return len(files), len(pages)


def tokens(value: Any) -> set[str]:
    return {token.lower() for token in re.findall(r"[0-9A-Za-z가-힣]+", str(value or "")) if len(token) >= 2}


def candidate_score(row: dict[str, Any], detail_candidate: dict[str, Any]) -> float:
    detail = str(detail_candidate.get("detail") or "")
    review_text = " ".join(
        str(row.get(key) or "")
        for key in ["source_excerpt_for_review", "source_file"]
    ).lower()
    score = 0.0
    if detail and detail.lower() in review_text:
        score += 8.0
    for hint in DETAIL_HINTS.get(detail, []):
        if hint.lower() in review_text:
            score += 4.0

    candidate_text = " ".join(
        [
            detail,
            " ".join(
                str(obj.get(key) or "")
                for obj in (detail_candidate.get("learning_objective_candidates") or [])[:3]
                for key in ["major_unit", "unit", "objective"]
            ),
        ]
    )
    overlap = tokens(review_text) & tokens(candidate_text)
    score += min(len(overlap), 10) * 0.5
    objective_scores = [
        float(obj.get("score") or 0)
        for obj in (detail_candidate.get("learning_objective_candidates") or [])[:2]
    ]
    if objective_scores:
        score += min(max(objective_scores), 8.0) * 0.25
    return score


def selected_detail_candidates(row: dict[str, Any], max_details: int) -> list[dict[str, Any]]:
    candidates = row.get("detail_candidates") or []
    scored = [(candidate_score(row, candidate), candidate) for candidate in candidates]
    scored.sort(key=lambda item: (-item[0], str(item[1].get("detail") or "")))
    selected = [(score, candidate) for score, candidate in scored if score >= 2.0][:max_details]
    if not selected and scored:
        selected = [scored[0]]
    output = []
    for score, candidate in selected:
        candidate = dict(candidate)
        candidate["detail_assignment_score"] = round(score, 3)
        output.append(candidate)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detail-review", type=Path, default=DEFAULT_DETAIL_REVIEW)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--min-refs", type=int, default=2)
    parser.add_argument("--max-refs", type=int, default=8)
    parser.add_argument("--max-packages-per-area", type=int, default=12)
    parser.add_argument("--max-details-per-chunk", type=int, default=1)
    args = parser.parse_args()

    rows = read_jsonl(args.detail_review)
    grouped: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    skipped: list[dict[str, Any]] = []

    for row in rows:
        coarse = row.get("folder_authoritative_scope") or {}
        detail_candidates = selected_detail_candidates(row, args.max_details_per_chunk)
        if not detail_candidates:
            skipped.append(
                {
                    "rag_input_id": row.get("rag_input_id"),
                    "reason": "missing_detail_candidates",
                    "source_file": row.get("source_file"),
                    "page_or_slide": row.get("page_or_slide"),
                }
            )
            continue
        for detail_candidate in detail_candidates:
            objective = best_objective(detail_candidate)
            if not objective:
                continue
            scope = {
                "period": coarse.get("period") or "",
                "subject": coarse.get("subject") or "",
                "field": coarse.get("field") or "",
                "area": coarse.get("area") or "",
                "detail": detail_candidate.get("detail") or "",
                "scope_id": detail_candidate.get("scope_id") or "",
            }
            key = (
                scope["period"],
                scope["subject"],
                scope["field"],
                scope["area"],
                scope["detail"],
            )
            package = grouped.setdefault(
                key,
                {
                    "package_rebuild_id": f"folder_detail_semantic_{short_hash({'scope': scope})}",
                    "created_at": now_iso(),
                    "status": "pending_folder_detail_semantic_review",
                    "expansion_kind": "folder_authoritative_detail_review",
                    "scope": scope,
                    "learning_objective": {
                        "learning_objective_id": objective.get("objective_id"),
                        "objective_id": objective.get("objective_id"),
                        "objective": objective.get("objective"),
                        "level": objective.get("level"),
                        "major_unit": objective.get("major_unit"),
                        "unit": objective.get("unit"),
                        "mapping_method": "folder_area_detail_candidate_top_objective",
                        "score": objective.get("score"),
                    },
                    "candidate_refs": [],
                    "review_requirements": {
                        "confirm_detail_alignment_inside_folder_area": True,
                        "confirm_learning_objective_alignment": True,
                        "confirm_not_visual_table_formula_law": True,
                        "confirm_content_can_ground_new_wording": True,
                        "minimum_refs_to_promote": args.min_refs,
                    },
                    "source_text_included": False,
                },
            )
            if len(package["candidate_refs"]) < args.max_refs:
                package["candidate_refs"].append(ref_from_review(row))

    packages = list(grouped.values())
    for package in packages:
        refs = package.get("candidate_refs") or []
        files, pages = source_diversity_score(refs)
        package["candidate_ref_count"] = len(refs)
        package["source_diversity"] = {"source_files": files, "source_pages": pages}
        if len(refs) < args.min_refs:
            package["status"] = "needs_more_refs_before_semantic_review"
        else:
            package["status"] = "ready_for_folder_detail_semantic_review"

    ready = [package for package in packages if package["status"] == "ready_for_folder_detail_semantic_review"]
    not_ready = [package for package in packages if package["status"] != "ready_for_folder_detail_semantic_review"]

    by_area: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for package in ready:
        by_area[(package.get("scope") or {}).get("area") or ""].append(package)

    selected: list[dict[str, Any]] = []
    for area, area_packages in by_area.items():
        area_packages.sort(
            key=lambda package: (
                -package.get("candidate_ref_count", 0),
                -package.get("source_diversity", {}).get("source_pages", 0),
                (package.get("scope") or {}).get("detail") or "",
            )
        )
        selected.extend(area_packages[: args.max_packages_per_area])

    selected.sort(
        key=lambda package: (
            (package.get("scope") or {}).get("subject") or "",
            (package.get("scope") or {}).get("field") or "",
            (package.get("scope") or {}).get("area") or "",
            (package.get("scope") or {}).get("detail") or "",
        )
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    all_path = args.output_dir / "folder_detail_semantic_review_candidates_all.jsonl"
    ready_path = args.output_dir / "folder_detail_semantic_review_candidates_ready.jsonl"
    selected_path = args.output_dir / "folder_detail_semantic_review_candidates_selected.jsonl"
    not_ready_path = args.output_dir / "folder_detail_semantic_review_candidates_not_ready.jsonl"
    skipped_path = args.output_dir / "folder_detail_semantic_review_candidates_skipped.jsonl"
    report_json_path = args.output_dir / "folder_detail_semantic_review_candidate_report.json"
    report_md_path = args.output_dir / "folder_detail_semantic_review_candidate_report.md"

    write_jsonl(all_path, packages)
    write_jsonl(ready_path, ready)
    write_jsonl(selected_path, selected)
    write_jsonl(not_ready_path, not_ready)
    write_jsonl(skipped_path, skipped)

    report = {
        "created_at": now_iso(),
        "inputs": {"detail_review": str(args.detail_review)},
        "outputs": {
            "all_candidates": str(all_path),
            "ready_candidates": str(ready_path),
            "selected_candidates": str(selected_path),
            "not_ready_candidates": str(not_ready_path),
            "skipped": str(skipped_path),
            "report_json": str(report_json_path),
            "report_md": str(report_md_path),
        },
        "counts": {
            "input_review_chunks": len(rows),
            "all_candidates": len(packages),
            "ready_candidates": len(ready),
            "selected_candidates": len(selected),
            "not_ready_candidates": len(not_ready),
            "skipped_chunks": len(skipped),
            "ready_by_area": dict(Counter((pkg.get("scope") or {}).get("area") for pkg in ready)),
            "selected_by_area": dict(Counter((pkg.get("scope") or {}).get("area") for pkg in selected)),
            "status_counts": dict(Counter(pkg.get("status") for pkg in packages)),
        },
        "policy": {
            "source_text_included": False,
            "automatic_generation_approval_granted": False,
            "question_generation_performed": False,
            "folder_area_is_authoritative": True,
        },
    }
    write_json(report_json_path, report)
    lines = [
        "# 폴더 기준 세부항목 Semantic Review 후보 보고서",
        "",
        f"- 생성 시각: {report['created_at']}",
        f"- 입력 검수 chunk: {len(rows)}",
        f"- 전체 후보 패키지: {len(packages)}",
        f"- semantic review 준비 완료: {len(ready)}",
        f"- 이번 선택 후보: {len(selected)}",
        f"- 준비 부족 후보: {len(not_ready)}",
        "",
        "## 선택 후보 영역 분포",
    ]
    for area, count in report["counts"]["selected_by_area"].items():
        lines.append(f"- {area}: {count}")
    lines.extend(
        [
            "",
            "## 출력",
            f"- 선택 후보: `{selected_path}`",
            f"- 전체 후보: `{all_path}`",
            f"- 준비 부족 후보: `{not_ready_path}`",
            "",
            "## 정책",
            "- 이 파일은 Codex/LLM semantic review 입력 후보이며 자동 생성 승인이 아니다.",
            "- 원문 텍스트는 포함하지 않고 근거 ID와 메타데이터만 저장한다.",
        ]
    )
    report_md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "input_review_chunks": len(rows),
                "ready_candidates": len(ready),
                "selected_candidates": len(selected),
                "not_ready_candidates": len(not_ready),
                "report_json": str(report_json_path),
                "selected": str(selected_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
