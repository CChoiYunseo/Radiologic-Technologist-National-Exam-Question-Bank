import peewee as pw

orm = wiz.model("portal/season/orm")
base = orm.base("base")


class Model(base):
    class Meta:
        db_table = "question_bank_candidate_validation"

    id = pw.CharField(max_length=48, primary_key=True)
    candidate_id = pw.CharField(max_length=40, index=True)
    validation_stage = pw.CharField(max_length=40, default="", index=True)
    validator_type = pw.CharField(max_length=32, default="", index=True)
    verdict = pw.CharField(max_length=32, default="", index=True)
    passed = pw.IntegerField(default=0, index=True)
    revision_required = pw.IntegerField(default=0, index=True)
    result_path = pw.TextField(default="")
    summary_json = pw.TextField(default="{}")
    created = pw.DateTimeField(index=True)
