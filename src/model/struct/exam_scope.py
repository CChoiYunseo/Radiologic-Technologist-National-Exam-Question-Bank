class ExamScope:
    def __init__(self, core):
        self.core = core
        self.rules = core.rules
        self.db = core.orm.use("exam_scope")
        self._index = None

    def data(self):
        return self.rules.exam_scope

    def blueprint(self):
        return self.rules.get("blueprint")

    def _normalize(self, value):
        return str(value or "").strip()

    def _build_index(self):
        data = self.data()
        rows = []
        lookup = set()

        verified_rows = data.get("verified_detail_rows") or []
        if verified_rows:
            for row in verified_rows:
                record = dict(
                    period=self._normalize(row.get("period")),
                    subject=self._normalize(row.get("subject")),
                    field=self._normalize(row.get("field")),
                    area=self._normalize(row.get("area")),
                    detail=self._normalize(row.get("detail")),
                    question_count=row.get("question_count") if isinstance(row.get("question_count"), int) else 0,
                    count_mode=self._normalize(row.get("count_mode") or "fixed"),
                    source_page=row.get("source_page"),
                    verification=self._normalize(row.get("verification")),
                )
                rows.append(record)
                lookup.add(self._key(record))
            self._index = dict(rows=rows, lookup=lookup)
            return self._index

        for subject in data.get("subjects", []):
            subject_name = self._normalize(subject.get("name"))
            for field in subject.get("fields", []):
                field_name = self._normalize(field.get("name"))
                for area in field.get("areas", []):
                    area_name = self._normalize(area.get("name"))
                    details = area.get("details") or []
                    if not details:
                        record = dict(
                            subject=subject_name,
                            field=field_name,
                            area=area_name,
                            detail="",
                            question_count=0,
                            count_mode="fixed",
                            source_page=None,
                        )
                        rows.append(record)
                        lookup.add(self._key(record))
                    for detail in details:
                        record = dict(
                            subject=subject_name,
                            field=field_name,
                            area=area_name,
                            detail=self._normalize(detail.get("name")),
                            question_count=0,
                            count_mode="fixed",
                            source_page=detail.get("source_page"),
                        )
                        rows.append(record)
                        lookup.add(self._key(record))

        self._index = dict(rows=rows, lookup=lookup)
        return self._index

    def _key(self, data):
        return (
            self._normalize(data.get("subject")),
            self._normalize(data.get("field")),
            self._normalize(data.get("area")),
            self._normalize(data.get("detail")),
        )

    def rows(self, include_practical=True):
        index = self._index or self._build_index()
        rows = index["rows"]
        if include_practical:
            return rows
        return [row for row in rows if row.get("subject") != "4. 실기시험"]

    def subjects(self, include_practical=True):
        rows = self.rows(include_practical=include_practical)
        seen = []
        for row in rows:
            item = dict(period=row.get("period", ""), subject=row.get("subject", ""))
            if item not in seen:
                seen.append(item)
        return seen

    def fields(self, subject="", include_practical=True):
        rows = self.rows(include_practical=include_practical)
        values = []
        for row in rows:
            if subject and row.get("subject") != subject:
                continue
            item = dict(period=row.get("period", ""), subject=row.get("subject", ""), field=row.get("field", ""))
            if item not in values:
                values.append(item)
        return values

    def areas(self, subject="", field="", include_practical=True):
        rows = self.rows(include_practical=include_practical)
        values = []
        for row in rows:
            if subject and row.get("subject") != subject:
                continue
            if field and row.get("field") != field:
                continue
            item = dict(period=row.get("period", ""), subject=row.get("subject", ""), field=row.get("field", ""), area=row.get("area", ""))
            if item not in values:
                values.append(item)
        return values

    def details(self, subject="", field="", area="", include_practical=True):
        rows = self.rows(include_practical=include_practical)
        values = []
        for row in rows:
            if subject and row.get("subject") != subject:
                continue
            if field and row.get("field") != field:
                continue
            if area and row.get("area") != area:
                continue
            values.append(row)
        return values

    def find(self, subject="", field="", area="", detail="", period=""):
        for row in self.rows(include_practical=True):
            if period and row.get("period") != period:
                continue
            if self._normalize(row.get("subject")) != self._normalize(subject):
                continue
            if self._normalize(row.get("field")) != self._normalize(field):
                continue
            if self._normalize(row.get("area")) != self._normalize(area):
                continue
            if self._normalize(row.get("detail")) != self._normalize(detail):
                continue
            return row
        return None

    def exists(self, subject, field="", area="", detail=""):
        index = self._index or self._build_index()
        data = dict(subject=subject, field=field, area=area, detail=detail)
        return self._key(data) in index["lookup"]

    def validate_scope(self, data, mvp_only=True):
        subject = self._normalize(data.get("subject"))
        field = self._normalize(data.get("field"))
        area = self._normalize(data.get("area"))
        detail = self._normalize(data.get("detail"))

        errors = []
        if mvp_only and subject in ["4. 실기시험", "실기시험"]:
            errors.append(dict(code="SCOPE_MVP_EXCLUDED", message="현재 MVP에서는 3교시 실기시험 문항을 생성하지 않습니다."))

        if not self.exists(subject, field, area, detail):
            errors.append(dict(code="SCOPE_NOT_FOUND", message="출제범위가 기준 데이터에 없습니다."))

        return dict(ok=len(errors) == 0, errors=errors)

    def strategy_for(self, subject, field="", area="", detail=""):
        strategy = self.rules.scope_generation_strategy
        key = self._key(dict(subject=subject, field=field, area=area, detail=detail))
        for row in strategy.get("rows", []):
            if self._key(row) == key:
                return row
        return None

    def seed_rows(self):
        rows = []
        for index, row in enumerate(self.rows(include_practical=True), start=1):
            seed = dict(row)
            seed["id"] = f"scope-{index:04d}"
            seed["period"] = row.get("period") or self._period_for(row.get("subject"))
            seed["is_mvp"] = row.get("subject") not in ["4. 실기시험", "실기시험"]
            strategy = self.strategy_for(row.get("subject"), row.get("field"), row.get("area"), row.get("detail"))
            if strategy:
                seed["recommended_question_types"] = strategy.get("recommended_question_types", [])
                seed["recommended_competency_types"] = strategy.get("recommended_competency_types", [])
                seed["recommended_difficulties"] = strategy.get("recommended_difficulties", [])
            rows.append(seed)
        return rows

    def sync_seed(self):
        """exam_scope.json 기준 데이터를 DB에 upsert한다."""
        rows = self.seed_rows()
        for row in rows:
            data = dict(
                id=row["id"],
                period=row.get("period", ""),
                subject=row.get("subject", ""),
                field=row.get("field", ""),
                area=row.get("area", ""),
                detail=row.get("detail", ""),
                question_count=row.get("question_count") or 0,
                count_mode=row.get("count_mode") or "fixed",
                source_page=row.get("source_page") or 0,
                is_mvp=1 if row.get("is_mvp") else 0,
                recommended_question_types=",".join(row.get("recommended_question_types", [])),
                recommended_competency_types=",".join(row.get("recommended_competency_types", [])),
                recommended_difficulties=",".join(row.get("recommended_difficulties", [])),
            )
            self.db.upsert(data, keys="id")
        return dict(total=len(rows), mvp=sum(1 for row in rows if row.get("is_mvp")))

    def db_rows(self, include_practical=True):
        kwargs = {}
        if not include_practical:
            kwargs["is_mvp"] = 1
        return self.db.rows(orderby="id", order="ASC", dump=1000, page=1, **kwargs)

    def summary(self):
        rows = self.rows(include_practical=True)
        fixed_total = sum(row.get("question_count") or 0 for row in rows)
        flexible_total = 0
        try:
            flexible_total = self.blueprint().get("totals", {}).get("flexible_question_sum", 0)
        except Exception:
            flexible_total = 0
        by_period = {}
        by_subject = {}
        for row in rows:
            count = row.get("question_count") or 0
            period = row.get("period") or self._period_for(row.get("subject"))
            subject = row.get("subject", "")
            by_period[period] = by_period.get(period, 0) + count
            by_subject[subject] = by_subject.get(subject, 0) + count
        if flexible_total:
            by_period["3교시"] = by_period.get("3교시", 0) + flexible_total
            by_subject["실기시험"] = by_subject.get("실기시험", 0) + flexible_total
        return dict(
            total=fixed_total + flexible_total,
            fixed_total=fixed_total,
            flexible_total=flexible_total,
            rows=len(rows),
            by_period=by_period,
            by_subject=by_subject,
        )

    def _period_for(self, subject):
        subject = self._normalize(subject)
        if subject in ["1. 방사선이론", "2. 의료관계법규", "방사선이론", "의료법규", "의료관계법규"]:
            return "1교시"
        if subject in ["3. 방사선응용", "방사선응용"]:
            return "2교시"
        if subject in ["4. 실기시험", "실기시험"]:
            return "3교시"
        return ""


Model = ExamScope
