import datetime
import json


class QuestionGenerationRequest:
    def __init__(self, core):
        self.core = core
        self.db = core.orm.use("question_generation_request")
        self.exam_scope = core.exam_scope
        self.rag = core.rag
        self.generator = core.question_generator

    def _now(self):
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _normalize(self, value):
        return str(value or "").strip()

    def create(self, data, created_by=""):
        scope = {
            "period": self._normalize(data.get("period")),
            "subject": self._normalize(data.get("subject")),
            "field": self._normalize(data.get("field")),
            "area": self._normalize(data.get("area")),
            "detail": self._normalize(data.get("detail")),
        }
        found = self.exam_scope.find(**scope)
        if found is None:
            raise Exception("선택한 출제범위가 기준표에 없습니다.")

        question_count = int(data.get("question_count") or 1)
        if question_count < 1:
            raise Exception("문항 수는 1 이상이어야 합니다.")

        request_data = {
            "scope": scope,
            "question_count": question_count,
            "difficulty": self._normalize(data.get("difficulty")),
            "question_type": self._normalize(data.get("question_type")),
            "focus": self._normalize(data.get("focus")),
            "top_k": int(data.get("top_k") or 6),
        }
        payload = self.rag.build_generation_input(request_data)
        now = self._now()
        row = {
            "status": "evidence_ready",
            "period": scope["period"],
            "subject": scope["subject"],
            "field": scope["field"],
            "area": scope["area"],
            "detail": scope["detail"],
            "question_count": question_count,
            "difficulty": payload["difficulty"],
            "question_type": payload["question_type"],
            "request_payload": json.dumps(payload, ensure_ascii=False),
            "created_by": created_by or "",
            "created": now,
            "updated": now,
        }
        request_id = self.db.insert(row)
        row["id"] = request_id
        row["request_payload"] = payload
        return row

    def list(self, page=1, dump=50):
        rows = self.db.rows(orderby="created", order="DESC", page=page, dump=dump)
        for row in rows:
            try:
                row["request_payload"] = json.loads(row.get("request_payload") or "{}")
            except Exception:
                row["request_payload"] = {}
        return rows

    def get(self, id):
        row = self.db.get(id=id)
        if row is None:
            raise Exception("생성 요청을 찾지 못했습니다.")
        try:
            row["request_payload"] = json.loads(row.get("request_payload") or "{}")
        except Exception:
            row["request_payload"] = {}
        return row

    def _update_payload(self, row, payload, status):
        now = self._now()
        self.db.update(dict(
            status=status,
            request_payload=json.dumps(payload, ensure_ascii=False),
            updated=now,
        ), id=row["id"])
        row["status"] = status
        row["request_payload"] = payload
        row["updated"] = now
        return row

    def run_generation(self, id, generated_override=None):
        row = self.get(id)
        payload = row.get("request_payload") or {}
        try:
            result = self.generator.run(payload, generated_override=generated_override)
            payload["generation_result"] = result
            payload["generated_question"] = result.get("validated_questions", [])
            payload["validation_report"] = [
                item.get("validation_report") for item in result.get("validated_questions", [])
            ]
            payload["final_status"] = result.get("final_status")
            status = result.get("final_status") or "needs_review"
            return self._update_payload(row, payload, status)
        except Exception as e:
            payload["generation_error"] = dict(
                message=str(e),
                occurred_at=self._now(),
            )
            return self._update_payload(row, payload, "generation_failed")


Model = QuestionGenerationRequest
