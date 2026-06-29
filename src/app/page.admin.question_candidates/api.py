import json
import os
import base64
import sqlite3
import uuid
from datetime import datetime

session = wiz.model("portal/season/session").use()

BASE_DIR = "/opt/app/project/main"
STORE_PATH = os.path.join(
    BASE_DIR,
    "resources",
    "generated",
    "question_bank_candidates",
    "question_bank_candidates.sqlite",
)
VISUAL_SVG_INDEX_PATH = os.path.join(
    BASE_DIR,
    "resources",
    "generated",
    "visual_assets_svg",
    "visual_svg_asset_index.jsonl",
)

ALLOWED_STATUSES = {
    "pending_expert_review",
    "needs_revision",
    "expert_rejected",
    "expert_passed",
    "expert_approved",
}


def _now():
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _connect():
    if not os.path.exists(STORE_PATH):
        wiz.response.status(404, message="문제은행 후보 저장소를 찾을 수 없습니다.")
    conn = sqlite3.connect(STORE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _row(row):
    return dict(row) if row else None


def _loads(value, default=None):
    if default is None:
        default = {}
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _draft_item(item):
    payload = item.get("candidate_payload") or {}
    draft = payload.get("draft_item") or {}
    return draft if isinstance(draft, dict) else {}


def _read_visual_svg_index():
    if not os.path.exists(VISUAL_SVG_INDEX_PATH):
        return {}
    rows = {}
    try:
        with open(VISUAL_SVG_INDEX_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                approval_id = row.get("source_visual_approval_id") or ""
                if approval_id:
                    rows[approval_id] = row
    except Exception:
        return {}
    return rows


def _candidate_visual_approval_id(item):
    if item.get("source_stage") == "visual_draft" and item.get("validation_package_id"):
        return item.get("validation_package_id")
    refs = item.get("evidence_refs") or []
    if isinstance(refs, list):
        for ref in refs:
            if isinstance(ref, dict) and str(ref.get("rag_input_id") or "").startswith("vqga_"):
                return ref.get("rag_input_id")
    payload = item.get("candidate_payload") or {}
    visual = payload.get("visual_evidence_summary") or {}
    return visual.get("source_visual_approval_id") or ""


def _visual_asset_for_candidate(item):
    approval_id = _candidate_visual_approval_id(item)
    if not approval_id:
        return None
    index = _read_visual_svg_index()
    asset = index.get(approval_id)
    if not asset:
        return {
            "source_visual_approval_id": approval_id,
            "available": False,
            "message": "연결된 SVG 도식이 없습니다.",
        }
    svg_path = asset.get("svg_path") or ""
    svg_markup = ""
    if svg_path and os.path.exists(svg_path):
        try:
            svg_markup = open(svg_path, "r", encoding="utf-8").read()
        except Exception:
            svg_markup = ""
    return {
        "available": bool(svg_markup),
        "source_visual_approval_id": approval_id,
        "asset_id": asset.get("asset_id") or "",
        "source_visual_kind": asset.get("source_visual_kind") or "",
        "template": asset.get("template") or "",
        "caption": asset.get("caption") or "",
        "source_file": asset.get("source_file") or "",
        "page_or_slide": asset.get("page_or_slide") or "",
        "svg_path": svg_path,
        "spec_path": asset.get("spec_path") or "",
        "status": asset.get("status") or "",
        "policy": asset.get("policy") or {},
        "svg_markup": svg_markup,
        "svg_data_url": (
            "data:image/svg+xml;base64," + base64.b64encode(svg_markup.encode("utf-8")).decode("ascii")
            if svg_markup
            else ""
        ),
        "message": "" if svg_markup else "SVG 파일을 읽을 수 없습니다.",
    }


def _hydrate_candidate(item):
    draft = _draft_item(item)

    if not item.get("options"):
        item["options"] = draft.get("options") or []
    if not item.get("explanation"):
        item["explanation"] = draft.get("explanation") or ""
    if not item.get("stem"):
        item["stem"] = draft.get("stem") or ""
    if not item.get("answer"):
        item["answer"] = draft.get("answer") or ""
    if not item.get("question_type"):
        item["question_type"] = draft.get("question_type") or ""
    if not item.get("difficulty"):
        item["difficulty"] = draft.get("difficulty") or ""
    if not item.get("competency_type"):
        item["competency_type"] = draft.get("competency_type") or ""
    if not item.get("distractor_strategy"):
        item["distractor_strategy"] = draft.get("distractor_strategy") or ""
    return item


def _int(value, default=1):
    try:
        return int(value)
    except Exception:
        return default


def _filters():
    status = wiz.request.query("status", "").strip()
    subject = wiz.request.query("subject", "").strip()
    source_stage = wiz.request.query("source_stage", "").strip()
    question_type = wiz.request.query("question_type", "").strip()
    keyword = wiz.request.query("keyword", "").strip()
    params = []
    clauses = []

    if status:
        clauses.append("status = ?")
        params.append(status)
    if subject:
        clauses.append("subject = ?")
        params.append(subject)
    if source_stage:
        clauses.append("source_stage = ?")
        params.append(source_stage)
    if question_type:
        clauses.append("question_type = ?")
        params.append(question_type)
    if keyword:
        clauses.append("(stem LIKE ? OR area LIKE ? OR detail LIKE ? OR learning_objective_id LIKE ? OR source_stage LIKE ?)")
        token = "%" + keyword + "%"
        params.extend([token, token, token, token, token])

    where = ""
    if clauses:
        where = " WHERE " + " AND ".join(clauses)
    return where, params


def summary():
    conn = _connect()
    try:
        status_rows = conn.execute(
            "SELECT status, COUNT(*) AS count FROM question_bank_candidate GROUP BY status ORDER BY status"
        ).fetchall()
        subject_rows = conn.execute(
            "SELECT subject, COUNT(*) AS count FROM question_bank_candidate GROUP BY subject ORDER BY count DESC, subject"
        ).fetchall()
        stage_rows = conn.execute(
            "SELECT source_stage, COUNT(*) AS count FROM question_bank_candidate GROUP BY source_stage ORDER BY count DESC, source_stage"
        ).fetchall()
        validation_rows = conn.execute(
            """
            SELECT validation_stage, verdict, COUNT(*) AS count
            FROM question_bank_candidate_validation
            GROUP BY validation_stage, verdict
            ORDER BY validation_stage, verdict
            """
        ).fetchall()
        question_type_rows = conn.execute(
            "SELECT question_type, COUNT(*) AS count FROM question_bank_candidate GROUP BY question_type ORDER BY count DESC, question_type"
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) AS count FROM question_bank_candidate").fetchone()["count"]
    finally:
        conn.close()

    wiz.response.status(
        200,
        total=total,
        statuses=[_row(item) for item in status_rows],
        subjects=[_row(item) for item in subject_rows],
        stages=[_row(item) for item in stage_rows],
        validations=[_row(item) for item in validation_rows],
        question_types=[_row(item) for item in question_type_rows],
    )


def candidates():
    page = max(_int(wiz.request.query("page", 1), 1), 1)
    dump = min(max(_int(wiz.request.query("dump", 20), 20), 5), 100)
    offset = (page - 1) * dump
    where, params = _filters()

    conn = _connect()
    try:
        total = conn.execute("SELECT COUNT(*) AS count FROM question_bank_candidate" + where, params).fetchone()["count"]
        rows = conn.execute(
            """
            SELECT
                q.id,
                q.status,
                q.source_stage,
                q.period,
                q.subject,
                q.field,
                q.area,
                q.detail,
                q.learning_objective_id,
                q.question_type,
                q.competency_type,
                q.difficulty,
                q.stem,
                q.answer,
                q.source_paths_json,
                q.candidate_payload_json,
                q.created,
                q.updated,
                (
                    SELECT COUNT(*)
                    FROM question_bank_candidate_evidence e
                    WHERE e.candidate_id = q.id
                ) AS evidence_count,
                (
                    SELECT COUNT(*)
                    FROM question_bank_candidate_validation v
                    WHERE v.candidate_id = q.id
                ) AS validation_count
            FROM question_bank_candidate q
            """
            + where
            + """
            ORDER BY q.updated DESC, q.created DESC, q.id
            LIMIT ? OFFSET ?
            """,
            params + [dump, offset],
        ).fetchall()
    finally:
        conn.close()

    wiz.response.status(200, rows=[_row(item) for item in rows], total=total, page=page, dump=dump)


def detail():
    candidate_id = wiz.request.query("id", "").strip()
    if not candidate_id:
        wiz.response.status(400, message="후보 ID가 필요합니다.")

    conn = _connect()
    try:
        candidate = conn.execute(
            "SELECT * FROM question_bank_candidate WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        if not candidate:
            wiz.response.status(404, message="후보 문항을 찾을 수 없습니다.")

        evidence = conn.execute(
            """
            SELECT id, rag_input_id, source_chunk_id, source_file, source_path, page_or_slide,
                   content_sha256, mapped_scope_id, mapping_confidence, evidence_role, created
            FROM question_bank_candidate_evidence
            WHERE candidate_id = ?
            ORDER BY page_or_slide, id
            """,
            (candidate_id,),
        ).fetchall()
        validations = conn.execute(
            """
            SELECT id, validation_stage, validator_type, verdict, passed, revision_required,
                   result_path, summary_json, created
            FROM question_bank_candidate_validation
            WHERE candidate_id = ?
            ORDER BY created, validation_stage
            """,
            (candidate_id,),
        ).fetchall()
    finally:
        conn.close()

    item = _row(candidate)
    item["options"] = _loads(item.get("options_json"), [])
    item["evidence_refs"] = _loads(item.get("evidence_refs_json"), [])
    item["source_paths"] = _loads(item.get("source_paths_json"), [])
    item["validation_summary"] = _loads(item.get("validation_summary_json"), {})
    item["candidate_payload"] = _loads(item.get("candidate_payload_json"), {})
    item = _hydrate_candidate(item)
    item["visual_asset"] = _visual_asset_for_candidate(item)
    item.pop("options_json", None)
    item.pop("evidence_refs_json", None)
    item.pop("source_paths_json", None)
    item.pop("validation_summary_json", None)
    item.pop("candidate_payload_json", None)

    validation_rows = []
    for row in validations:
        data = _row(row)
        data["summary"] = _loads(data.get("summary_json"), {})
        data.pop("summary_json", None)
        validation_rows.append(data)

    wiz.response.status(
        200,
        candidate=item,
        evidence=[_row(row) for row in evidence],
        validations=validation_rows,
    )


def update_status():
    candidate_id = wiz.request.query("id", "").strip()
    status = wiz.request.query("status", "").strip()
    note = wiz.request.query("note", "").strip()

    if not candidate_id:
        wiz.response.status(400, message="후보 ID가 필요합니다.")
    if status not in ALLOWED_STATUSES:
        wiz.response.status(400, message="허용되지 않은 검수 상태입니다.")

    conn = _connect()
    try:
        row = conn.execute("SELECT id FROM question_bank_candidate WHERE id = ?", (candidate_id,)).fetchone()
        if not row:
            wiz.response.status(404, message="후보 문항을 찾을 수 없습니다.")

        now = _now()
        conn.execute(
            "UPDATE question_bank_candidate SET status = ?, updated = ? WHERE id = ?",
            (status, now, candidate_id),
        )
        conn.execute(
            """
            INSERT INTO question_bank_candidate_validation (
                id, candidate_id, validation_stage, validator_type, verdict, passed,
                revision_required, result_path, summary_json, created
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "qbcv_" + uuid.uuid4().hex[:16],
                candidate_id,
                "expert_review",
                "human",
                status,
                1 if status in {"expert_passed", "expert_approved"} else 0,
                1 if status == "needs_revision" else 0,
                "",
                json.dumps(
                    {
                        "note": note,
                        "reviewer": session.get("id", ""),
                        "reviewer_name": session.get("name", ""),
                    },
                    ensure_ascii=False,
                ),
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    wiz.response.status(200, id=candidate_id, status=status, updated=now)
