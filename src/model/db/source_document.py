import peewee as pw

orm = wiz.model("portal/season/orm")
base = orm.base("base")


class Model(base):
    class Meta:
        db_table = "source_document"

    id = pw.CharField(max_length=32, primary_key=True)
    title = pw.CharField(max_length=240, default="", index=True)
    filename = pw.CharField(max_length=240, default="")
    path = pw.TextField(default="")
    source_type = pw.CharField(max_length=40, default="subject_reference", index=True)
    period = pw.CharField(max_length=16, default="", index=True)
    subject = pw.CharField(max_length=80, default="", index=True)
    field = pw.CharField(max_length=120, default="", index=True)
    area = pw.CharField(max_length=160, default="", index=True)
    detail = pw.CharField(max_length=240, default="", index=True)
    page_count = pw.IntegerField(default=0)
    text_pages = pw.IntegerField(default=0)
    low_text_pages = pw.TextField(default="")
    extraction_status = pw.CharField(max_length=32, default="pending", index=True)
    copyright_status = pw.CharField(max_length=32, default="unknown", index=True)
    reference_date = pw.CharField(max_length=20, default="")
    created = pw.DateTimeField(index=True)
    updated = pw.DateTimeField()
