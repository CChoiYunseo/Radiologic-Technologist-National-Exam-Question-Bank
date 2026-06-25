import peewee as pw

orm = wiz.model("portal/season/orm")
base = orm.base("base")


class Model(base):
    class Meta:
        db_table = "question_bank_candidate"

    id = pw.CharField(max_length=40, primary_key=True)
    status = pw.CharField(max_length=40, default="pending_expert_review", index=True)
    source_stage = pw.CharField(max_length=40, default="", index=True)
    package_id = pw.CharField(max_length=40, default="", index=True)
    review_candidate_id = pw.CharField(max_length=40, default="", index=True)
    validation_package_id = pw.CharField(max_length=40, default="", index=True)
    revision_package_id = pw.CharField(max_length=40, default="", index=True)
    period = pw.CharField(max_length=16, default="", index=True)
    subject = pw.CharField(max_length=80, default="", index=True)
    field = pw.CharField(max_length=120, default="", index=True)
    area = pw.CharField(max_length=160, default="", index=True)
    detail = pw.CharField(max_length=240, default="", index=True)
    scope_id = pw.CharField(max_length=40, default="", index=True)
    learning_objective_id = pw.CharField(max_length=40, default="", index=True)
    question_type = pw.CharField(max_length=40, default="", index=True)
    competency_type = pw.CharField(max_length=40, default="", index=True)
    difficulty = pw.CharField(max_length=16, default="", index=True)
    stem = pw.TextField(default="")
    option_1 = pw.TextField(default="")
    option_2 = pw.TextField(default="")
    option_3 = pw.TextField(default="")
    option_4 = pw.TextField(default="")
    option_5 = pw.TextField(default="")
    options_json = pw.TextField(default="[]")
    answer = pw.IntegerField(default=0, index=True)
    explanation = pw.TextField(default="")
    distractor_strategy = pw.TextField(default="")
    evidence_refs_json = pw.TextField(default="[]")
    source_paths_json = pw.TextField(default="{}")
    validation_summary_json = pw.TextField(default="{}")
    candidate_payload_json = pw.TextField(default="{}")
    created = pw.DateTimeField(index=True)
    updated = pw.DateTimeField()
