#!/usr/bin/env python3
"""Classify ready semantic KOs and correct clear scope mismatches.

This curation step keeps source text out of the output. It only rewrites KO
metadata when an objective's official target unit clearly disagrees with the
KO scope detail.
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
DEFAULT_KOS = PROJECT_ROOT / "resources/generated/knowledge_objects_v2_semantic/knowledge_objects_v2_semantic.jsonl"
DEFAULT_SAFE_PACKAGES = (
    PROJECT_ROOT / "resources/generated/safe_generation_packages_v3_semantic/safe_generation_packages_v3_ready.jsonl"
)
DEFAULT_FEEDBACK = (
    PROJECT_ROOT
    / "resources/generated/package_quality_feedback_planner_v1_semantic_style_v4_stratified8_revalidated/package_improvement_worklist.jsonl"
)
DEFAULT_TARGETS = PROJECT_ROOT / "resources/rules/question_generation_targets.json"
DEFAULT_BLUEPRINT = PROJECT_ROOT / "resources/rules/blueprint.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "resources/generated/knowledge_objects_v2_semantic_curated"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(prefix: str, payload: Any) -> str:
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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


def compact_key(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", str(value or "").lower())


def iter_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for child in value.values():
            found.extend(iter_dicts(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(iter_dicts(child))
    return found


def target_by_objective_id(path: Path) -> dict[str, dict[str, Any]]:
    data = read_json(path)
    targets = data.get("targets") if isinstance(data, dict) else data
    mapping: dict[str, dict[str, Any]] = {}
    for row in iter_dicts(targets):
        objective_id = row.get("learning_objective_id") or row.get("objective_id")
        if objective_id and row.get("unit"):
            mapping[str(objective_id)] = row
    return mapping


def canonical_scopes(path: Path) -> dict[str, list[dict[str, Any]]]:
    data = read_json(path)
    by_detail: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in iter_dicts(data):
        if row.get("row_type") != "detail" or not row.get("detail"):
            continue
        scope = {
            "period": row.get("period") or "",
            "subject": row.get("subject") or "",
            "field": row.get("field") or "",
            "area": row.get("area") or "",
            "detail": row.get("detail") or "",
        }
        by_detail[compact_key(row.get("detail"))].append(scope)
    return by_detail


def best_canonical_scope(current: dict[str, Any], official_detail: str, by_detail: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    candidates = by_detail.get(compact_key(official_detail)) or []
    if not candidates:
        return None
    # Folder-authoritative mapping is fixed. Scope correction is allowed only
    # inside the same exam context; cross-area target units are treated as
    # learning-objective unit labels, not as proof that the KO scope should move.
    same_context = [
        row
        for row in candidates
        if row.get("period") == current.get("period")
        and row.get("subject") == current.get("subject")
        and row.get("field") == current.get("field")
        and row.get("area") == current.get("area")
    ]
    if len(same_context) == 1:
        return dict(same_context[0])
    return None


def objective_id_for(obj: dict[str, Any]) -> str:
    objective = obj.get("learning_objective") or {}
    return str(objective.get("learning_objective_id") or objective.get("objective_id") or "")


def scope_with_id(scope: dict[str, Any]) -> dict[str, Any]:
    output = {key: scope.get(key) or "" for key in ["period", "subject", "field", "area", "detail"]}
    output["scope_id"] = stable_id("folder_scope", output)
    return output


def package_feedback_by_ko(safe_packages: list[dict[str, Any]], feedback_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    package_to_ko = {row.get("package_id"): row.get("knowledge_object_id") for row in safe_packages}
    feedback_by_ko: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in feedback_rows:
        package_id = str(row.get("source_package_rebuild_id") or "")
        if package_id.startswith("planned_"):
            package_id = package_id.removeprefix("planned_")
        ko_id = package_to_ko.get(package_id)
        if ko_id:
            feedback_by_ko[str(ko_id)].append(row)
    return feedback_by_ko


def feedback_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    actions = Counter(str(row.get("package_quality_action") or "unknown") for row in rows)
    verdicts: Counter[str] = Counter()
    checks: Counter[str] = Counter()
    recommended: Counter[str] = Counter()
    for row in rows:
        verdicts.update(row.get("verdict_counts") or {})
        checks.update(row.get("check_counts") or {})
        recommended.update(row.get("recommended_actions") or {})
    return {
        "actions": dict(actions),
        "verdicts": dict(verdicts),
        "checks": dict(checks),
        "recommended_actions": dict(recommended),
        "reviewed": bool(rows),
    }


def correct_scope_if_clear(
    obj: dict[str, Any],
    targets: dict[str, dict[str, Any]],
    canonical_by_detail: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    output = json.loads(json.dumps(obj, ensure_ascii=False))
    scope = dict(output.get("scope") or {})
    objective_id = objective_id_for(output)
    target = targets.get(objective_id) or {}
    official_detail = str(target.get("unit") or "")
    if not official_detail or compact_key(official_detail) == compact_key(scope.get("detail")):
        return output, None

    canonical = best_canonical_scope(scope, official_detail, canonical_by_detail)
    if not canonical:
        return output, None

    old_scope = scope_with_id(scope)
    new_scope = scope_with_id(canonical)
    output["scope"] = new_scope
    old_detail = str(old_scope.get("detail") or "")
    new_detail = str(new_scope.get("detail") or "")
    for key in ("answerable_point", "content_tag"):
        value = str(output.get(key) or "")
        if value.startswith(f"{old_detail}:"):
            output[key] = f"{new_detail}:{value.split(':', 1)[1]}"
        elif old_detail and old_detail in value:
            output[key] = value.replace(old_detail, new_detail, 1)
    correction = {
        "type": "scope_mismatch_corrected",
        "objective_id": objective_id,
        "official_detail": official_detail,
        "old_scope": old_scope,
        "new_scope": new_scope,
    }
    output["curation_scope_correction"] = correction
    return output, correction


def classify(obj: dict[str, Any], feedback: dict[str, Any], correction: dict[str, Any] | None) -> tuple[str, str, list[str]]:
    reasons: list[str] = []
    pair = obj.get("direct_evidence_pair_quality") or {}
    objective = obj.get("learning_objective") or {}
    actions = set((feedback.get("actions") or {}).keys())
    checks = set((feedback.get("checks") or {}).keys())
    verdicts = feedback.get("verdicts") or {}
    score_min = float(pair.get("score_min") or 0)
    objective_score = float(objective.get("score") or 0)
    objective_terms = set(pair.get("objective_terms") or [])
    shared_focus = set(pair.get("shared_focus_terms") or [])

    if obj.get("generation_readiness") != "ready":
        reasons.append(f"not_ready:{obj.get('generation_readiness')}")
    if obj.get("hold_reasons"):
        reasons.extend(f"existing_hold:{reason}" for reason in obj.get("hold_reasons") or [])
    if len(obj.get("use_direct_refs") or []) < 2:
        reasons.append("use_direct_refs_lt_2")
    if obj.get("objective_link_confidence") != "direct":
        reasons.append("objective_link_not_direct")
    if pair.get("risk_reasons"):
        reasons.extend(f"direct_evidence_risk:{reason}" for reason in pair.get("risk_reasons") or [])

    hard_reasons = [reason for reason in reasons if reason.startswith(("not_ready:", "existing_hold:", "direct_evidence_risk:"))]
    if correction and correction.get("type") == "scope_mismatch_unresolved":
        hard_reasons.append("scope_mismatch_unresolved")
    if "hold_until_evidence_replaced" in actions:
        hard_reasons.append("llm_feedback_hold_until_evidence_replaced")

    if hard_reasons:
        return "D", "hold", sorted(dict.fromkeys(hard_reasons))

    if correction and correction.get("type") == "scope_mismatch_corrected":
        reasons.append("scope_corrected_retest_required")

    if "requires_use_direct_ref_review" in actions and not correction:
        reasons.append("llm_feedback_requires_use_direct_ref_review")
    if {"evidence_grounding", "learning_objective_alignment", "scope_alignment"} & checks and not correction:
        reasons.append("llm_feedback_alignment_or_grounding_issue")
    if score_min < 8.5:
        reasons.append("direct_evidence_score_near_floor")
    if objective_score < 8.5:
        reasons.append("learning_objective_score_near_floor")
    if objective_terms and shared_focus and not objective_terms.issubset(shared_focus):
        reasons.append("partial_objective_focus_coverage")
    if "improve_planner_distractors_and_style" in actions:
        reasons.append("llm_feedback_planner_style_or_distractor_rework")

    if verdicts.get("reject") and not correction:
        return "C", "needs_rework", sorted(dict.fromkeys(reasons or ["llm_secondary_reject"]))
    if "llm_feedback_requires_use_direct_ref_review" in reasons or "llm_feedback_alignment_or_grounding_issue" in reasons:
        return "C", "needs_rework", sorted(dict.fromkeys(reasons))
    if verdicts.get("pass") and not correction and not (set(reasons) - {"partial_objective_focus_coverage"}):
        return "A", "ready", sorted(dict.fromkeys(reasons or ["llm_secondary_pass"]))
    if score_min >= 9.0 and objective_score >= 9.0 and not correction and not reasons:
        return "A", "ready", ["high_direct_evidence_quality"]
    if correction or reasons or not feedback.get("reviewed"):
        return "B", "ready", sorted(dict.fromkeys(reasons or ["untested_ready_retest_required"]))
    return "A", "ready", ["ready_high_confidence"]


def curate(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    kos = read_jsonl(args.knowledge_objects)
    safe_packages = read_jsonl(args.safe_packages)
    feedback_rows = read_jsonl(args.feedback)
    targets = target_by_objective_id(args.targets)
    canonical_by_detail = canonical_scopes(args.blueprint)
    feedback_by_ko = package_feedback_by_ko(safe_packages, feedback_rows)

    curated: list[dict[str, Any]] = []
    corrections: list[dict[str, Any]] = []
    now = now_iso()

    for obj in kos:
        corrected, correction = correct_scope_if_clear(obj, targets, canonical_by_detail)
        if correction:
            correction["knowledge_object_id"] = obj.get("knowledge_object_id")
            corrections.append(correction)
        summary = feedback_summary(feedback_by_ko.get(str(obj.get("knowledge_object_id")), []))
        grade, status, reasons = classify(corrected, summary, correction)
        corrected["generation_readiness"] = "ready" if status == "ready" else status
        corrected["quality_reclassification"] = {
            "curated_at": now,
            "grade": grade,
            "status": status,
            "reasons": reasons,
            "feedback_summary": summary,
            "scope_mismatch_detected": bool(correction),
            "scope_correction_type": (correction or {}).get("type"),
            "source_text_included": False,
        }
        if status != "ready":
            hold_reasons = list(corrected.get("hold_reasons") or [])
            hold_reasons.extend(f"quality_reclassification:{reason}" for reason in reasons)
            corrected["hold_reasons"] = sorted(dict.fromkeys(hold_reasons))
        curated.append(corrected)

    grade_counts = Counter(row["quality_reclassification"]["grade"] for row in curated)
    status_counts = Counter(row["quality_reclassification"]["status"] for row in curated)
    by_scope_grade: Counter[str] = Counter()
    for row in curated:
        scope = row.get("scope") or {}
        by_scope_grade[
            f"{scope.get('area') or ''} / {scope.get('detail') or ''} / {row['quality_reclassification']['grade']}"
        ] += 1

    report = {
        "created_at": now,
        "inputs": {
            "knowledge_objects": str(args.knowledge_objects),
            "safe_packages": str(args.safe_packages),
            "feedback": str(args.feedback),
            "targets": str(args.targets),
            "blueprint": str(args.blueprint),
        },
        "outputs": {
            "all": str(args.output_dir / "knowledge_objects_v2_semantic_curated_all.jsonl"),
            "ready": str(args.output_dir / "knowledge_objects_v2_semantic_curated_ready.jsonl"),
            "needs_rework": str(args.output_dir / "knowledge_objects_v2_semantic_curated_needs_rework.jsonl"),
            "hold": str(args.output_dir / "knowledge_objects_v2_semantic_curated_hold.jsonl"),
            "corrections": str(args.output_dir / "scope_corrections.jsonl"),
            "report_json": str(args.output_dir / "knowledge_objects_v2_semantic_curated_report.json"),
            "report_md": str(args.output_dir / "knowledge_objects_v2_semantic_curated_report.md"),
        },
        "counts": {
            "input_knowledge_objects": len(kos),
            "curated": len(curated),
            "by_grade": dict(grade_counts),
            "by_status": dict(status_counts),
            "scope_corrections": len([row for row in corrections if row.get("type") == "scope_mismatch_corrected"]),
            "scope_mismatch_unresolved": len([row for row in corrections if row.get("type") == "scope_mismatch_unresolved"]),
            "by_scope_grade": dict(by_scope_grade),
        },
        "policy": {
            "grades": {
                "A": "ready, high confidence or LLM secondary pass",
                "B": "ready but retest required",
                "C": "needs evidence/planner rework before generation",
                "D": "hold until evidence is replaced or mismatch is resolved",
            },
            "source_text_included": False,
            "scope_correction_basis": "learning_objective_id official unit in question_generation_targets plus canonical detail in blueprint",
        },
    }
    return curated, corrections, report


def write_markdown(path: Path, report: dict[str, Any], curated: list[dict[str, Any]], corrections: list[dict[str, Any]]) -> None:
    lines = ["# Semantic KO Curated Quality Report", "", "## Summary", ""]
    for key, value in report["counts"].items():
        if key != "by_scope_grade":
            lines.append(f"- {key}: {value}")
    lines.extend(["", "## Grade By Scope", "", "| scope | count |", "|---|---:|"])
    for key, value in sorted((report["counts"].get("by_scope_grade") or {}).items()):
        lines.append(f"| {key} | {value} |")
    lines.extend(["", "## Scope Corrections", "", "| KO | type | old detail | new detail |", "|---|---|---|---|"])
    for row in corrections:
        old_scope = row.get("old_scope") or {}
        new_scope = row.get("new_scope") or {}
        lines.append(
            f"| {row.get('knowledge_object_id')} | {row.get('type')} | {old_scope.get('detail') or row.get('current_detail') or ''} | {new_scope.get('detail') or row.get('official_detail') or ''} |"
        )
    lines.extend(["", "## Curated KOs", "", "| grade | status | KO | scope | objective | reasons |", "|---|---|---|---|---|---|"])
    for row in curated:
        quality = row.get("quality_reclassification") or {}
        scope = row.get("scope") or {}
        objective = row.get("learning_objective") or {}
        scope_label = " / ".join(str(scope.get(key) or "") for key in ["period", "subject", "field", "area", "detail"] if scope.get(key))
        lines.append(
            f"| {quality.get('grade')} | {quality.get('status')} | {row.get('knowledge_object_id')} | {scope_label} | {objective.get('learning_objective_id')} | {', '.join(quality.get('reasons') or [])} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge-objects", type=Path, default=DEFAULT_KOS)
    parser.add_argument("--safe-packages", type=Path, default=DEFAULT_SAFE_PACKAGES)
    parser.add_argument("--feedback", type=Path, default=DEFAULT_FEEDBACK)
    parser.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    parser.add_argument("--blueprint", type=Path, default=DEFAULT_BLUEPRINT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    curated, corrections, report = curate(args)
    ready = [row for row in curated if row.get("quality_reclassification", {}).get("status") == "ready"]
    needs_rework = [row for row in curated if row.get("quality_reclassification", {}).get("status") == "needs_rework"]
    hold = [row for row in curated if row.get("quality_reclassification", {}).get("status") == "hold"]

    write_jsonl(args.output_dir / "knowledge_objects_v2_semantic_curated_all.jsonl", curated)
    write_jsonl(args.output_dir / "knowledge_objects_v2_semantic_curated_ready.jsonl", ready)
    write_jsonl(args.output_dir / "knowledge_objects_v2_semantic_curated_needs_rework.jsonl", needs_rework)
    write_jsonl(args.output_dir / "knowledge_objects_v2_semantic_curated_hold.jsonl", hold)
    write_jsonl(args.output_dir / "scope_corrections.jsonl", corrections)
    write_json(args.output_dir / "knowledge_objects_v2_semantic_curated_report.json", report)
    write_markdown(args.output_dir / "knowledge_objects_v2_semantic_curated_report.md", report, curated, corrections)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
