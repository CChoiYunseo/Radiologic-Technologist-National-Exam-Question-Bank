import peewee as pw

orm = wiz.model("portal/season/orm")
base = orm.base("base")


class Model(base):
    class Meta:
        db_table = "question_generation_request"

    id = pw.CharField(max_length=32, primary_key=True)
    status = pw.CharField(max_length=24, default="draft", index=True)
    period = pw.CharField(max_length=16, default="", index=True)
    subject = pw.CharField(max_length=80, default="", index=True)
    field = pw.CharField(max_length=120, default="", index=True)
    area = pw.CharField(max_length=160, default="", index=True)
    detail = pw.CharField(max_length=240, default="", index=True)
    question_count = pw.IntegerField(default=1)
    difficulty = pw.CharField(max_length=16, default="")
    question_type = pw.CharField(max_length=40, default="")
    request_payload = pw.TextField(default="")
    created_by = pw.CharField(max_length=32, default="", index=True)
    created = pw.DateTimeField(index=True)
    updated = pw.DateTimeField()
