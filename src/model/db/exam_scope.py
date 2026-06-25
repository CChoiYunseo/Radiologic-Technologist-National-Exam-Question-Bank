import peewee as pw

orm = wiz.model("portal/season/orm")
base = orm.base("base")


class Model(base):
    class Meta:
        db_table = "exam_scope"

    id = pw.CharField(max_length=16, primary_key=True)
    period = pw.CharField(max_length=16, default="", index=True)
    subject = pw.CharField(max_length=80, default="", index=True)
    field = pw.CharField(max_length=120, default="", index=True)
    area = pw.CharField(max_length=160, default="", index=True)
    detail = pw.CharField(max_length=240, default="", index=True)
    question_count = pw.IntegerField(default=0, index=True)
    count_mode = pw.CharField(max_length=24, default="fixed", index=True)
    source_page = pw.IntegerField(default=0)
    is_mvp = pw.IntegerField(default=1, index=True)
    recommended_question_types = pw.TextField(default="")
    recommended_competency_types = pw.TextField(default="")
    recommended_difficulties = pw.CharField(max_length=32, default="")
