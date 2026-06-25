import json

struct = wiz.model("struct")
session = wiz.model("portal/season/session").use()


def _int(value, default=1):
    try:
        return int(value)
    except Exception:
        return default


def summary():
    data = struct.exam_scope.summary()
    db_count = struct.db("exam_scope").count() or 0
    request_count = struct.db("question_generation_request").count() or 0
    wiz.response.status(200, summary=data, db_count=db_count, request_count=request_count)


def seed():
    result = struct.exam_scope.sync_seed()
    wiz.response.status(200, result=result)


def subjects():
    rows = struct.exam_scope.subjects(include_practical=True)
    wiz.response.status(200, rows=rows)


def fields():
    subject = wiz.request.query("subject", "")
    rows = struct.exam_scope.fields(subject=subject, include_practical=True)
    wiz.response.status(200, rows=rows)


def areas():
    subject = wiz.request.query("subject", "")
    field = wiz.request.query("field", "")
    rows = struct.exam_scope.areas(subject=subject, field=field, include_practical=True)
    wiz.response.status(200, rows=rows)


def details():
    subject = wiz.request.query("subject", "")
    field = wiz.request.query("field", "")
    area = wiz.request.query("area", "")
    rows = struct.exam_scope.details(subject=subject, field=field, area=area, include_practical=True)
    wiz.response.status(200, rows=rows)


def create_request():
    data = dict(
        period=wiz.request.query("period", ""),
        subject=wiz.request.query("subject", ""),
        field=wiz.request.query("field", ""),
        area=wiz.request.query("area", ""),
        detail=wiz.request.query("detail", ""),
        question_count=_int(wiz.request.query("question_count", 1), 1),
        difficulty=wiz.request.query("difficulty", ""),
        question_type=wiz.request.query("question_type", ""),
        focus=wiz.request.query("focus", ""),
        top_k=_int(wiz.request.query("top_k", 6), 6),
        generation_mode="rag_evidence_request",
    )
    try:
        row = struct.question_generation_request.create(data, created_by=session.get("id", ""))
    except Exception as e:
        wiz.response.status(400, message=str(e))
    wiz.response.status(200, row=row)


def preview_rag():
    scope = dict(
        period=wiz.request.query("period", ""),
        subject=wiz.request.query("subject", ""),
        field=wiz.request.query("field", ""),
        area=wiz.request.query("area", ""),
        detail=wiz.request.query("detail", ""),
    )
    try:
        payload = struct.rag.build_generation_input(dict(
            scope=scope,
            question_count=_int(wiz.request.query("question_count", 1), 1),
            difficulty=wiz.request.query("difficulty", ""),
            question_type=wiz.request.query("question_type", ""),
            focus=wiz.request.query("focus", ""),
            top_k=_int(wiz.request.query("top_k", 6), 6),
        ))
    except Exception as e:
        wiz.response.status(400, message=str(e))
    wiz.response.status(200, payload=payload)


def requests():
    rows = struct.question_generation_request.list(page=1, dump=20)
    wiz.response.status(200, rows=rows)


def run_generation():
    request_id = wiz.request.query("id", "")
    if not request_id:
        wiz.response.status(400, message="생성 요청 ID가 필요합니다.")
    row = struct.question_generation_request.run_generation(request_id)
    wiz.response.status(200, row=row)
