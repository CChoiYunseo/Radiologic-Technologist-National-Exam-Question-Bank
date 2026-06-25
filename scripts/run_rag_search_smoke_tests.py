#!/usr/bin/env python3
"""Run smoke tests for the text-only RAG BM25 index."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "resources" / "extracted" / "rag_search_index_text_bm25" / "rag_text_bm25.sqlite"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources" / "reports"


TEST_CASES: list[dict[str, Any]] = [
    {
        "id": "public_health_epidemiology",
        "query": "역학 질병관리 감염병",
        "expected": {"period": "1교시", "subject": "방사선이론", "field": "기초의학", "area": "공중보건"},
    },
    {
        "id": "public_health_environment",
        "query": "환경보건 수질 대기 폐기물",
        "expected": {"period": "1교시", "subject": "방사선이론", "field": "기초의학", "area": "공중보건"},
    },
    {
        "id": "radiation_management_safety",
        "query": "방사선안전관리 차폐 구역 선량한도",
        "expected": {"period": "1교시", "subject": "방사선이론", "field": "방사선 장해방어", "area": "방사선관리"},
    },
    {
        "id": "radiation_monitoring",
        "query": "개인선량 모니터링 오염 측정",
        "expected": {"period": "1교시", "subject": "방사선이론", "field": "방사선 장해방어", "area": "방사선관리"},
    },
    {
        "id": "dosimetry_detector",
        "query": "방사선 검출기 전리함 섬광 계측",
        "expected": {"period": "1교시", "subject": "방사선이론", "field": "방사선취급", "area": "방사선계측"},
    },
    {
        "id": "dosimetry_absorbed_dose",
        "query": "조사선량 흡수선량 교정 계측",
        "expected": {"period": "1교시", "subject": "방사선이론", "field": "방사선취급", "area": "방사선계측"},
    },
    {
        "id": "radiobiology_cell",
        "query": "방사선생물 세포 DNA 염색체",
        "expected": {"period": "1교시", "subject": "방사선이론", "field": "방사선 장해방어", "area": "방사선생물"},
    },
    {
        "id": "radiobiology_embryo",
        "query": "배아 태아 유전적 영향 방사선",
        "expected": {"period": "1교시", "subject": "방사선이론", "field": "방사선 장해방어", "area": "방사선생물"},
    },
    {
        "id": "medical_image_info_digital",
        "query": "디지털 엑스선 영상 PACS 픽셀",
        "expected": {"period": "1교시", "subject": "방사선이론", "field": "방사선기초", "area": "의료영상정보"},
    },
    {
        "id": "medical_image_quality",
        "query": "영상 화질 MTF 노이즈 DQE",
        "expected": {"period": "1교시", "subject": "방사선이론", "field": "방사선기초", "area": "의료영상정보"},
    },
    {
        "id": "radiographic_positioning",
        "query": "방사선영상 촬영 자세 체위 투사",
        "expected": {"period": "2교시", "subject": "방사선응용", "field": "영상진단", "area": "방사선영상"},
    },
    {
        "id": "radiographic_pelvis",
        "query": "골반 촬영 방사선영상",
        "expected": {"period": "2교시", "subject": "방사선응용", "field": "영상진단", "area": "방사선영상"},
    },
    {
        "id": "fluoroscopy_contrast",
        "query": "투시 조영제 위장관 검사",
        "expected": {"period": "2교시", "subject": "방사선응용", "field": "영상진단", "area": "투시조영검사"},
    },
    {
        "id": "fluoroscopy_biliary",
        "query": "담도계 조영검사 투시",
        "expected": {"period": "2교시", "subject": "방사선응용", "field": "영상진단", "area": "투시조영검사"},
    },
    {
        "id": "angiography_catheter",
        "query": "혈관조영 카테터 가이드와이어",
        "expected": {"period": "2교시", "subject": "방사선응용", "field": "영상진단", "area": "심맥관 및 중재술"},
    },
    {
        "id": "angiography_coronary",
        "query": "관상동맥 혈관조영 중재술 스텐트",
        "expected": {"period": "2교시", "subject": "방사선응용", "field": "영상진단", "area": "심맥관 및 중재술"},
    },
    {
        "id": "ct_reconstruction",
        "query": "전산화단층 재구성 역투영 필터",
        "expected": {"period": "2교시", "subject": "방사선응용", "field": "영상진단", "area": "전산화단층검사"},
    },
    {
        "id": "ct_dose_quality",
        "query": "CT 선량 CTDI DLP 화질",
        "expected": {"period": "2교시", "subject": "방사선응용", "field": "영상진단", "area": "전산화단층검사"},
    },
    {
        "id": "ultrasound_doppler",
        "query": "초음파 도플러 혈류 속도",
        "expected": {"period": "2교시", "subject": "방사선응용", "field": "영상진단", "area": "초음파기술"},
    },
    {
        "id": "ultrasound_probe",
        "query": "초음파 탐촉자 압전 배열",
        "expected": {"period": "2교시", "subject": "방사선응용", "field": "영상진단", "area": "초음파기술"},
    },
    {
        "id": "nuclear_medicine_pet",
        "query": "핵의학 PET SPECT 섬광카메라",
        "expected": {"period": "2교시", "subject": "방사선응용", "field": "핵의학 검사"},
    },
    {
        "id": "nuclear_radiopharmaceutical",
        "query": "방사성의약품 집적 표지 순도",
        "expected": {"period": "2교시", "subject": "방사선응용", "field": "핵의학 검사"},
    },
    {
        "id": "therapy_planning",
        "query": "방사선치료 치료계획 선량분포",
        "expected": {"period": "2교시", "subject": "방사선응용", "field": "방사선 치료"},
    },
    {
        "id": "therapy_linac",
        "query": "선형가속기 광자선 전자선 치료",
        "expected": {"period": "2교시", "subject": "방사선응용", "field": "방사선 치료"},
    },
]


POLICY_PROBE_QUERIES = [
    "표 1-1",
    "그림 1-5",
    "수식 공식",
    "법 제14조 별표",
]


def query_tokens(query: str) -> list[str]:
    return [token for token in re.findall(r"[0-9A-Za-z가-힣]+", query) if token.strip()]


def fts_query(query: str) -> str:
    tokens = query_tokens(query)
    if not tokens:
        return f'"{query.replace(chr(34), chr(34) * 2)}"'
    return " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)


def run_search(conn: sqlite3.Connection, query: str, top_k: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            chunks.rag_input_id,
            chunks.source_file,
            chunks.page_or_slide,
            chunks.mapped_period,
            chunks.mapped_subject,
            chunks.mapped_field,
            chunks.mapped_area,
            chunks.mapped_detail,
            chunks.scope_mapping_status,
            chunks.scope_mapping_confidence,
            chunks.scope_mapping_needs_review,
            chunks.approved_for_rag_evidence,
            chunks.approved_for_generation,
            chunks.extraction_quality,
            chunks.candidate_rag_status,
            chunks.excerpt,
            bm25(rag_fts, 1.0, 0.35) AS score
        FROM rag_fts
        JOIN chunks ON chunks.doc_id = rag_fts.rowid
        WHERE rag_fts MATCH ?
        ORDER BY score
        LIMIT ?
        """,
        (fts_query(query), top_k),
    ).fetchall()
    results = []
    for rank, row in enumerate(rows, start=1):
        results.append(
            {
                "rank": rank,
                "score": row[16],
                "rag_input_id": row[0],
                "source_file": row[1],
                "page_or_slide": row[2],
                "period": row[3],
                "subject": row[4],
                "field": row[5],
                "area": row[6],
                "detail": row[7],
                "scope_mapping_status": row[8],
                "scope_mapping_confidence": row[9],
                "scope_mapping_needs_review": bool(row[10]),
                "approved_for_rag_evidence": bool(row[11]),
                "approved_for_generation": bool(row[12]),
                "extraction_quality": row[13],
                "candidate_rag_status": row[14],
                "excerpt": row[15],
            }
        )
    return results


def result_matches(result: dict[str, Any], expected: dict[str, str]) -> bool:
    for key, expected_value in expected.items():
        if expected_value and result.get(key) != expected_value:
            return False
    return True


def evaluate_case(conn: sqlite3.Connection, case: dict[str, Any], top_k: int) -> dict[str, Any]:
    results = run_search(conn, case["query"], top_k)
    expected = case["expected"]
    top1_match = bool(results) and result_matches(results[0], expected)
    any_topk_match = any(result_matches(result, expected) for result in results)
    policy_violations = [
        {
            "rank": result["rank"],
            "rag_input_id": result["rag_input_id"],
            "reason": "policy_or_quality_mismatch",
            "approved_for_rag_evidence": result["approved_for_rag_evidence"],
            "approved_for_generation": result["approved_for_generation"],
            "extraction_quality": result["extraction_quality"],
            "candidate_rag_status": result["candidate_rag_status"],
        }
        for result in results
        if not result["approved_for_rag_evidence"]
        or result["approved_for_generation"]
        or result["extraction_quality"] != "high"
        or result["candidate_rag_status"] != "ready_for_rag_evidence"
    ]
    status = "pass" if top1_match else "warn" if any_topk_match else "fail"
    return {
        "id": case["id"],
        "query": case["query"],
        "expected": expected,
        "status": status,
        "top1_match": top1_match,
        "any_topk_match": any_topk_match,
        "policy_violations": policy_violations,
        "top_results": results,
    }


def database_policy_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "chunk_count": conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
        "fts_count": conn.execute("SELECT COUNT(*) FROM rag_fts").fetchone()[0],
        "policy_mismatch_rows": conn.execute(
            """
            SELECT COUNT(*)
            FROM chunks
            WHERE approved_for_rag_evidence != 1
               OR approved_for_generation != 0
               OR extraction_quality != 'high'
               OR candidate_rag_status != 'ready_for_rag_evidence'
            """
        ).fetchone()[0],
        "needs_review_rows_in_index": conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE scope_mapping_needs_review = 1"
        ).fetchone()[0],
        "confidence_counts": dict(
            conn.execute(
                "SELECT scope_mapping_confidence, COUNT(*) FROM chunks GROUP BY scope_mapping_confidence"
            ).fetchall()
        ),
    }


def run_policy_probes(conn: sqlite3.Connection, top_k: int) -> list[dict[str, Any]]:
    probes = []
    for query in POLICY_PROBE_QUERIES:
        results = run_search(conn, query, top_k)
        probes.append(
            {
                "query": query,
                "result_count": len(results),
                "top_results": results,
                "policy_violations": [
                    result
                    for result in results
                    if not result["approved_for_rag_evidence"]
                    or result["approved_for_generation"]
                    or result["extraction_quality"] != "high"
                    or result["candidate_rag_status"] != "ready_for_rag_evidence"
                ],
            }
        )
    return probes


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# RAG 검색 품질 스모크 테스트 보고서",
        "",
        f"- 생성 시각: {report['created_at']}",
        f"- 테스트 DB: `{report['db_path']}`",
        f"- 테스트 질의: {report['summary']['test_count']}",
        f"- 통과: {report['summary']['pass_count']}",
        f"- 주의: {report['summary']['warn_count']}",
        f"- 실패: {report['summary']['fail_count']}",
        f"- Top-k 내 일치: {report['summary']['topk_match_count']}",
        f"- 정책 위반 결과: {report['summary']['policy_violation_count']}",
        "",
        "## DB 정책 요약",
    ]
    for key, value in report["database_policy_summary"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## 실패/주의 케이스"])
    flagged = [case for case in report["cases"] if case["status"] != "pass"]
    if not flagged:
        lines.append("- 없음")
    for case in flagged:
        top = case["top_results"][0] if case["top_results"] else {}
        lines.append(
            f"- {case['id']} `{case['query']}`: {case['status']} "
            f"(top1={top.get('period','')}/{top.get('subject','')}/{top.get('field','')}/{top.get('area','')}/{top.get('detail','')}, "
            f"source={top.get('source_file','')} p.{top.get('page_or_slide','')})"
        )
    lines.extend(["", "## 정책 탐침 질의"])
    for probe in report["policy_probes"]:
        lines.append(f"- `{probe['query']}`: 결과 {probe['result_count']}, 정책 위반 {len(probe['policy_violations'])}")
    lines.extend(
        [
            "",
            "## 주의",
            "- 이 테스트는 BM25 검색·메타데이터 연결 확인용입니다.",
            "- 의미적으로 애매한 질의는 다음 단계에서 임베딩 검색 또는 질의 확장 후보로 남깁니다.",
            "- 검색 결과 본문은 원문 재현 용도가 아니라 내부 근거 위치 확인용입니다.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    try:
        cases = [evaluate_case(conn, case, args.top_k) for case in TEST_CASES]
        policy_summary = database_policy_summary(conn)
        policy_probes = run_policy_probes(conn, args.top_k)
    finally:
        conn.close()

    status_counts = Counter(case["status"] for case in cases)
    policy_violation_count = sum(len(case["policy_violations"]) for case in cases)
    policy_probe_violation_count = sum(len(probe["policy_violations"]) for probe in policy_probes)
    report = {
        "version": "2026-06-24",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(args.db),
        "top_k": args.top_k,
        "summary": {
            "test_count": len(cases),
            "pass_count": status_counts.get("pass", 0),
            "warn_count": status_counts.get("warn", 0),
            "fail_count": status_counts.get("fail", 0),
            "top1_match_count": sum(1 for case in cases if case["top1_match"]),
            "topk_match_count": sum(1 for case in cases if case["any_topk_match"]),
            "policy_violation_count": policy_violation_count + policy_probe_violation_count,
        },
        "database_policy_summary": policy_summary,
        "cases": cases,
        "policy_probes": policy_probes,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "rag_search_smoke_test_report.json"
    md_path = args.output_dir / "rag_search_smoke_test_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(markdown_report(report), encoding="utf-8")

    print(
        json.dumps(
            {
                "test_count": report["summary"]["test_count"],
                "pass": report["summary"]["pass_count"],
                "warn": report["summary"]["warn_count"],
                "fail": report["summary"]["fail_count"],
                "policy_violations": report["summary"]["policy_violation_count"],
                "json_report": str(json_path),
                "md_report": str(md_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
