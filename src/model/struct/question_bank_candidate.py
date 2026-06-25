import json


class QuestionBankCandidate:
    def __init__(self, core):
        self.core = core
        self.db = core.orm.use("question_bank_candidate")
        self.evidence_db = core.orm.use("question_bank_candidate_evidence")
        self.validation_db = core.orm.use("question_bank_candidate_validation")

    def _loads(self, value, fallback):
        try:
            return json.loads(value or "")
        except Exception:
            return fallback

    def _normalize_row(self, row):
        if row is None:
            return None
        row["options"] = self._loads(row.get("options_json"), [])
        row["evidence_refs"] = self._loads(row.get("evidence_refs_json"), [])
        row["source_paths"] = self._loads(row.get("source_paths_json"), {})
        row["validation_summary"] = self._loads(row.get("validation_summary_json"), {})
        row["candidate_payload"] = self._loads(row.get("candidate_payload_json"), {})
        return row

    def list(self, status="", page=1, dump=50):
        where = {}
        if status:
            where["status"] = status
        rows = self.db.rows(orderby="created", order="DESC", page=page, dump=dump, **where)
        return [self._normalize_row(row) for row in rows]

    def get(self, id):
        row = self.db.get(id=id)
        if row is None:
            raise Exception("문항 후보를 찾지 못했습니다.")
        row = self._normalize_row(row)
        row["evidence"] = self.evidence_db.rows(candidate_id=id, dump=200)
        validations = self.validation_db.rows(candidate_id=id, orderby="created", order="ASC", dump=200)
        for item in validations:
            item["summary"] = self._loads(item.get("summary_json"), {})
        row["validations"] = validations
        return row

    def summary(self):
        rows = self.db.rows(dump=10000)
        status_counts = {}
        subject_counts = {}
        for row in rows:
            status = row.get("status") or ""
            subject = row.get("subject") or ""
            status_counts[status] = status_counts.get(status, 0) + 1
            subject_counts[subject] = subject_counts.get(subject, 0) + 1
        return dict(total=len(rows), status_counts=status_counts, subject_counts=subject_counts)


Model = QuestionBankCandidate
