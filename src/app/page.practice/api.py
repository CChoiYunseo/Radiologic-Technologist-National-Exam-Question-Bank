import json
import os
import random
import hashlib
import sqlite3
import uuid

BASE_DIR = "/opt/app/project/main"
STORE_PATH = os.path.join(
    BASE_DIR,
    "resources",
    "generated",
    "question_bank_candidates",
    "question_bank_candidates.sqlite",
)
ASSEMBLED_EXAM_DIR = os.path.join(BASE_DIR, "resources", "generated", "assembled_exams")
DEFAULT_EXAM_MODE = "strict_text_only"


def _connect():
    if not os.path.exists(STORE_PATH):
        wiz.response.status(404, message="연습 문제 저장소를 찾을 수 없습니다.")
    conn = sqlite3.connect(STORE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def _loads(value, default=None):
    if default is None:
        default = []
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _load_json(path, default=None):
    if default is None:
        default = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _assembled_exam_path(period, mode):
    safe_period = period if period in ["1교시", "2교시"] else "1교시"
    safe_mode = "strict_text_only"
    return os.path.join(ASSEMBLED_EXAM_DIR, "%s_%s.json" % (safe_period, safe_mode))


def _load_assembled_exam(period, mode):
    path = _assembled_exam_path(period, mode)
    exam = _load_json(path, {})
    if not exam:
        return None
    exam["path"] = path
    return exam


def _selectable_status(conn):
    published = conn.execute(
        "SELECT COUNT(*) AS count FROM question_bank_candidate WHERE status = 'expert_passed'"
    ).fetchone()["count"]
    if published > 0:
        return ["expert_passed"], False
    return ["pending_expert_review"], True


def _status_where(statuses):
    return "status IN (" + ",".join(["?"] * len(statuses)) + ")"


def _base_options(row):
    data = _loads(row["options_json"], [])
    values = []
    for index, item in enumerate(data):
        if isinstance(item, dict):
            text = item.get("text") or item.get("content") or ""
        else:
            text = str(item or "")
        if text.strip():
            values.append({"original_index": index + 1, "text": text.strip()})

    if len(values) >= 5:
        return values[:5]

    values = []
    for index in range(1, 6):
        text = row["option_%d" % index] or ""
        values.append({"original_index": index, "text": text.strip()})
    return values


def _shuffle_seed(session_id, question_id):
    value = "%s|%s" % (session_id or "", question_id or "")
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:16], 16)


def _display_options(row, session_id):
    values = list(_base_options(row))
    if session_id:
        rng = random.Random(_shuffle_seed(session_id, row["id"]))
        rng.shuffle(values)
    return [
        {
            "index": index,
            "text": option["text"],
        }
        for index, option in enumerate(values, start=1)
    ]


def _display_answer(row, session_id, selected_display):
    values = list(_base_options(row))
    if session_id:
        rng = random.Random(_shuffle_seed(session_id, row["id"]))
        rng.shuffle(values)
    correct_original = _int(row["answer"], 0)
    selected_original = 0
    correct_display = 0
    answer_text = ""
    for display_index, option in enumerate(values, start=1):
        if display_index == selected_display:
            selected_original = option["original_index"]
        if option["original_index"] == correct_original:
            correct_display = display_index
            answer_text = option["text"]
    return selected_original, correct_display, answer_text


def _question(row, number=None, session_id=""):
    item = dict(row)
    data = {
        "id": item["id"],
        "number": number,
        "period": item.get("period") or "",
        "subject": item.get("subject") or "",
        "field": item.get("field") or "",
        "area": item.get("area") or "",
        "detail": item.get("detail") or "",
        "question_type": item.get("question_type") or "",
        "difficulty": item.get("difficulty") or "",
        "stem": item.get("stem") or "",
        "options": _display_options(row, session_id),
    }
    return data


def summary():
    conn = _connect()
    try:
        statuses, preview = _selectable_status(conn)
        where = _status_where(statuses)
        rows = conn.execute(
            """
            SELECT subject, COUNT(*) AS count
            FROM question_bank_candidate
            WHERE """ + where + """
            GROUP BY subject
            ORDER BY count DESC, subject
            """,
            statuses,
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) AS count FROM question_bank_candidate WHERE " + where,
            statuses,
        ).fetchone()["count"]
    finally:
        conn.close()

    wiz.response.status(
        200,
        total=total,
        preview=preview,
        subjects=[dict(row) for row in rows],
        exams=[
            _load_assembled_exam("1교시", "strict_text_only"),
            _load_assembled_exam("2교시", "strict_text_only"),
        ],
    )


def start():
    subject = wiz.request.query("subject", "").strip()
    period = wiz.request.query("period", "").strip()
    mode = DEFAULT_EXAM_MODE
    count = min(max(_int(wiz.request.query("count", 10), 10), 1), 30)

    if period:
        exam = _load_assembled_exam(period, mode)
        if not exam:
            wiz.response.status(404, message="조립된 시험지를 찾을 수 없습니다.")
        question_ids = [
            item.get("candidate_id")
            for item in exam.get("questions", [])
            if item.get("candidate_id")
        ]
        if not question_ids:
            wiz.response.status(404, message="시험지에 포함된 문항이 없습니다.")

        conn = _connect()
        try:
            placeholders = ",".join(["?"] * len(question_ids))
            db_rows = conn.execute(
                """
                SELECT id, period, subject, field, area, detail, question_type, difficulty,
                       stem, option_1, option_2, option_3, option_4, option_5, options_json
                FROM question_bank_candidate
                WHERE id IN (""" + placeholders + """)
                """,
                question_ids,
            ).fetchall()
        finally:
            conn.close()

        row_by_id = {row["id"]: row for row in db_rows}
        session_id = "exam_" + uuid.uuid4().hex[:16]
        ordered_rows = [row_by_id[qid] for qid in question_ids if qid in row_by_id]
        questions = [_question(row, index + 1, session_id) for index, row in enumerate(ordered_rows)]
        wiz.response.status(
            200,
            session_id=session_id,
            total=len(questions),
            preview=False,
            exam={
                "period": exam.get("period"),
                "mode": "strict_text_only",
                "target_questions": exam.get("target_questions"),
                "selected_questions": exam.get("selected_questions"),
                "complete": exam.get("complete"),
                "subject_targets": exam.get("subject_targets") or {},
                "subject_counts": exam.get("subject_counts") or {},
                "warnings": exam.get("warnings") or [],
            },
            questions=questions,
        )
        return

    conn = _connect()
    try:
        statuses, preview = _selectable_status(conn)
        clauses = [_status_where(statuses)]
        params = list(statuses)
        if subject:
            clauses.append("subject = ?")
            params.append(subject)
        where = " AND ".join(clauses)

        rows = conn.execute(
            """
            SELECT id, period, subject, field, area, detail, question_type, difficulty,
                   stem, option_1, option_2, option_3, option_4, option_5, options_json
            FROM question_bank_candidate
            WHERE """ + where + """
            ORDER BY RANDOM()
            LIMIT ?
            """,
            params + [count],
        ).fetchall()
    finally:
        conn.close()

    session_id = "practice_" + uuid.uuid4().hex[:16]
    questions = [_question(row, index + 1, session_id) for index, row in enumerate(rows)]
    wiz.response.status(
        200,
        session_id=session_id,
        total=len(questions),
        preview=preview,
        questions=questions,
    )


def answer():
    question_id = wiz.request.query("id", "").strip()
    session_id = wiz.request.query("session_id", "").strip()
    selected = _int(wiz.request.query("selected", 0), 0)
    if not question_id:
        wiz.response.status(400, message="문항 ID가 필요합니다.")
    if selected < 1 or selected > 5:
        wiz.response.status(400, message="답안을 선택해주세요.")

    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT id, status, answer, explanation,
                   option_1, option_2, option_3, option_4, option_5, options_json
            FROM question_bank_candidate
            WHERE id = ?
            """,
            (question_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        wiz.response.status(404, message="문항을 찾을 수 없습니다.")

    selected_original, correct_answer, answer_text = _display_answer(row, session_id, selected)

    wiz.response.status(
        200,
        id=question_id,
        selected=selected,
        correct_answer=correct_answer,
        correct=selected_original == _int(row["answer"], 0),
        answer_text=answer_text,
        explanation=row["explanation"] or "",
    )
