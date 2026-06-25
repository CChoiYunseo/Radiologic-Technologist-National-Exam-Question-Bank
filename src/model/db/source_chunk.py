import peewee as pw

orm = wiz.model("portal/season/orm")
base = orm.base("base")


class Model(base):
    class Meta:
        db_table = "source_chunk"

    id = pw.CharField(max_length=32, primary_key=True)
    document_id = pw.CharField(max_length=32, index=True)
    chunk_index = pw.IntegerField(default=0, index=True)
    page_start = pw.IntegerField(default=0, index=True)
    page_end = pw.IntegerField(default=0)
    period = pw.CharField(max_length=16, default="", index=True)
    subject = pw.CharField(max_length=80, default="", index=True)
    field = pw.CharField(max_length=120, default="", index=True)
    area = pw.CharField(max_length=160, default="", index=True)
    detail = pw.CharField(max_length=240, default="", index=True)
    content = pw.TextField(default="")
    content_hash = pw.CharField(max_length=64, default="", index=True)
    token_estimate = pw.IntegerField(default=0)
    extraction_method = pw.CharField(max_length=32, default="text")
    embedding_id = pw.CharField(max_length=64, default="", index=True)
    created = pw.DateTimeField(index=True)
