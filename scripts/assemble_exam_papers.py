#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone


BASE_DIR = "/opt/app/project/main"
DEFAULT_DB = os.path.join(
    BASE_DIR,
    "resources",
    "generated",
    "question_bank_candidates",
    "question_bank_candidates.sqlite",
)
DEFAULT_BLUEPRINT = os.path.join(BASE_DIR, "resources", "rules", "blueprint.json")
DEFAULT_OUTPUT = os.path.join(BASE_DIR, "resources", "generated", "assembled_exams")
DEFAULT_SHORTAGE_OUTPUT = os.path.join(BASE_DIR, "resources", "generated", "text_question_shortage_worklist")

TARGET_PERIODS = ("1교시", "2교시")
USABLE_STATUSES = ("expert_passed", "pending_expert_review")
VISUAL_SOURCE_STAGE = "visual_draft"


def stable_int(value):
    return int(hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16], 16)


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_blueprint(path):
    data = load_json(path)
    rows = [
        row
        for row in data.get("detail_distribution", [])
        if row.get("period") in TARGET_PERIODS and row.get("row_type", "detail") == "detail"
    ]
    period_targets = {
        period: int(info.get("questions", 0))
        for period, info in data.get("period_distribution", {}).items()
        if period in TARGET_PERIODS
    }
    subject_targets = {}
    for row in data.get("subject_distribution", []):
        if row.get("period") in TARGET_PERIODS:
            subject_targets[(row.get("period"), row.get("subject"))] = int(row.get("question_count", 0))
    return rows, period_targets, subject_targets


def connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def load_candidates(db_path):
    conn = connect(db_path)
    try:
        placeholders = ",".join(["?"] * len(USABLE_STATUSES))
        query = f"""
            SELECT id, status, source_stage, period, subject, field, area, detail,
                   question_type, difficulty, stem,
                   option_1, option_2, option_3, option_4, option_5, options_json,
                   answer, explanation
            FROM question_bank_candidate
            WHERE status IN ({placeholders})
              AND period IN ('1교시', '2교시')
        """
        params = list(USABLE_STATUSES)
        query += " AND COALESCE(source_stage, '') != ?"
        params.append(VISUAL_SOURCE_STAGE)
        rows = [dict(row) for row in conn.execute(query, params).fetchall()]
    finally:
        conn.close()
    return rows


def scope_key(row):
    return (
        row.get("period") or "",
        row.get("subject") or "",
        row.get("field") or "",
        row.get("area") or "",
        row.get("detail") or "",
    )


def option_values(row):
    values = []
    try:
        parsed = json.loads(row.get("options_json") or "[]")
    except Exception:
        parsed = []
    for item in parsed:
        if isinstance(item, dict):
            text = item.get("text") or item.get("content") or ""
        else:
            text = str(item or "")
        if text.strip():
            values.append(text.strip())
    if len(values) >= 5:
        return values[:5]
    return [(row.get(f"option_{idx}") or "").strip() for idx in range(1, 6)]


def candidate_sort_key(row):
    stage_priority = {
        "semantic_reviewed_rewrite_retry_llm_pass": 0,
        "semantic_reviewed_rewrite_llm_pass": 1,
        "semantic_reviewed_full_run_llm_pass": 2,
        "subject_quota_llm_pass": 3,
        "revised_llm_pass": 4,
        "recovered_llm_pass": 5,
        "initial_llm_pass": 6,
        "visual_draft": 9,
    }
    return (
        stage_priority.get(row.get("source_stage") or "", 8),
        stable_int(row.get("id") or ""),
    )


def display_question(row, number):
    return {
        "number": number,
        "candidate_id": row["id"],
        "period": row.get("period") or "",
        "subject": row.get("subject") or "",
        "field": row.get("field") or "",
        "area": row.get("area") or "",
        "detail": row.get("detail") or "",
        "source_stage": row.get("source_stage") or "",
        "question_type": row.get("question_type") or "",
        "difficulty": row.get("difficulty") or "",
        "stem": row.get("stem") or "",
        "options": [{"index": idx, "text": text} for idx, text in enumerate(option_values(row), start=1)],
    }


def build_period_exam(period, detail_rows, target_count, subject_targets, candidates, mode):
    by_key = defaultdict(list)
    by_period = defaultdict(list)
    for row in candidates:
        by_key[scope_key(row)].append(row)
        by_period[row.get("period") or ""].append(row)
    for items in by_key.values():
        items.sort(key=candidate_sort_key)
    for items in by_period.values():
        items.sort(key=candidate_sort_key)

    selected = []
    selected_ids = set()
    gaps = []
    quota_rows = [row for row in detail_rows if row.get("period") == period]

    for quota in quota_rows:
        key = scope_key(quota)
        need = int(quota.get("question_count") or 0)
        pool = [row for row in by_key.get(key, []) if row["id"] not in selected_ids]
        take = pool[:need]
        for row in take:
            selected.append(row)
            selected_ids.add(row["id"])
        if len(take) < need:
            gaps.append(
                {
                    "period": quota.get("period"),
                    "subject": quota.get("subject"),
                    "field": quota.get("field"),
                    "area": quota.get("area"),
                    "detail": quota.get("detail"),
                    "target": need,
                    "selected": len(take),
                    "shortage": need - len(take),
                }
            )

    if len(selected) < target_count:
        for row in by_period.get(period, []):
            if row["id"] in selected_ids:
                continue
            selected.append(row)
            selected_ids.add(row["id"])
            if len(selected) >= target_count:
                break

    selected = selected[:target_count]
    subject_counts = Counter((row.get("subject") or "") for row in selected)
    source_stage_counts = Counter((row.get("source_stage") or "") for row in selected)

    warnings = []
    if len(selected) < target_count:
        warnings.append(f"{period} 목표 {target_count}문항 중 {len(selected)}문항만 조립되었습니다.")
    return {
        "exam_id": f"{period}_{mode}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        "mode": mode,
        "period": period,
        "target_questions": target_count,
        "selected_questions": len(selected),
        "complete": len(selected) == target_count,
        "subject_targets": {
            subject: target
            for (target_period, subject), target in subject_targets.items()
            if target_period == period
        },
        "subject_counts": dict(subject_counts),
        "source_stage_counts": dict(source_stage_counts),
        "gap_count": sum(item["shortage"] for item in gaps),
        "gaps": gaps,
        "warnings": warnings,
        "questions": [display_question(row, idx + 1) for idx, row in enumerate(selected)],
    }


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def is_law_gap(row):
    joined = " ".join(str(row.get(key) or "") for key in ["subject", "field", "area", "detail"])
    return any(token in joined for token in ["법규", "의료법", "의료기사", "지역보건법", "방사선관계법규"])


def build_shortage_worklist(gap_rows):
    worklist = []
    held = []
    for row in gap_rows:
        if row.get("mode") != "strict_text_only":
            continue
        target = {
            "period": row.get("period"),
            "subject": row.get("subject"),
            "field": row.get("field"),
            "area": row.get("area"),
            "detail": row.get("detail"),
            "needed_question_count": int(row.get("shortage") or 0),
            "current_selected_count": int(row.get("selected") or 0),
            "target_question_count": int(row.get("target") or 0),
            "generation_mode": "new_text_question_required",
            "source": "assembled_exam_strict_text_only_gap",
            "policy": {
                "visual_draft_must_not_be_used_as_substitute": True,
                "visual_table_formula_materials_excluded": True,
                "must_use_generation_safe_text_evidence": True,
                "must_write_new_wording": True,
            },
        }
        if is_law_gap(row):
            target["generation_mode"] = "held_for_latest_law_review"
            target["policy"]["latest_law_review_required"] = True
            held.append(target)
        else:
            worklist.append(target)
    return worklist, held


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--blueprint", default=DEFAULT_BLUEPRINT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--shortage-output", default=DEFAULT_SHORTAGE_OUTPUT)
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.shortage_output, exist_ok=True)
    detail_rows, period_targets, subject_targets = load_blueprint(args.blueprint)

    built = []
    reports = []
    mode = "strict_text_only"
    candidates = load_candidates(args.db)
    for period in TARGET_PERIODS:
        exam = build_period_exam(
            period=period,
            detail_rows=detail_rows,
            target_count=period_targets[period],
            subject_targets=subject_targets,
            candidates=candidates,
            mode=mode,
        )
        filename = f"{period}_{mode}.json"
        path = os.path.join(args.output, filename)
        write_json(path, exam)
        built.append({"period": period, "mode": mode, "path": path, **{k: exam[k] for k in ["target_questions", "selected_questions", "complete", "gap_count"]}})
        for gap in exam["gaps"]:
            reports.append({"mode": mode, **gap})

    worklist, held = build_shortage_worklist(reports)
    write_jsonl(os.path.join(args.shortage_output, "text_question_shortage_worklist.jsonl"), worklist)
    write_jsonl(os.path.join(args.shortage_output, "law_review_held_shortage_worklist.jsonl"), held)

    report = {
        "created": datetime.now(timezone.utc).isoformat(),
        "output_dir": args.output,
        "shortage_output_dir": args.shortage_output,
        "built": built,
        "shortage_counts": {
            "new_text_question_targets": len(worklist),
            "new_text_question_count": sum(int(row.get("needed_question_count") or 0) for row in worklist),
            "held_for_law_review_targets": len(held),
            "held_for_law_review_question_count": sum(int(row.get("needed_question_count") or 0) for row in held),
        },
        "notes": [
            "strict_text_only는 visual_draft를 제외한 실제 텍스트형 후보만 사용합니다.",
            "visual_draft 후보는 텍스트 시험지 부족분 대체로 사용하지 않습니다.",
            "부족분은 신규 텍스트 문항 생성 대상 worklist로 분리했습니다.",
        ],
    }
    write_json(os.path.join(args.output, "assembled_exam_report.json"), report)
    write_jsonl(os.path.join(args.output, "assembled_exam_gaps.jsonl"), reports)

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
