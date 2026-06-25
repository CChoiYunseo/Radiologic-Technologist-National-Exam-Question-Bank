import json
import os
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


def _selectable_status(conn):
    published = conn.execute(
        "SELECT COUNT(*) AS count FROM question_bank_candidate WHERE status = 'expert_passed'"
    ).fetchone()["count"]
    if published > 0:
        return ["expert_passed"], False
    return ["pending_expert_review"], True


def _status_where(statuses):
    return "status IN (" + ",".join(["?"] * len(statuses)) + ")"


def _options(row):
    data = _loads(row["options_json"], [])
    values = []
    for index, item in enumerate(data):
        if isinstance(item, dict):
            text = item.get("text") or item.get("content") or ""
        else:
            text = str(item or "")
        if text.strip():
            values.append({"index": index + 1, "text": text.strip()})

    if len(values) >= 5:
        return values[:5]

    values = []
    for index in range(1, 6):
        text = row["option_%d" % index] or ""
        values.append({"index": index, "text": text.strip()})
    return values


def _question(row, number=None):
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
        "options": _options(row),
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
    )


def start():
    subject = wiz.request.query("subject", "").strip()
    count = min(max(_int(wiz.request.query("count", 10), 10), 1), 30)

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

    questions = [_question(row, index + 1) for index, row in enumerate(rows)]
    wiz.response.status(
        200,
        session_id="practice_" + uuid.uuid4().hex[:16],
        total=len(questions),
        preview=preview,
        questions=questions,
    )


def answer():
    question_id = wiz.request.query("id", "").strip()
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

    correct_answer = _int(row["answer"], 0)
    options = _options(row)
    answer_text = ""
    for option in options:
        if option["index"] == correct_answer:
            answer_text = option["text"]
            break

    wiz.response.status(
        200,
        id=question_id,
        selected=selected,
        correct_answer=correct_answer,
        correct=selected == correct_answer,
        answer_text=answer_text,
        explanation=row["explanation"] or "",
    )
