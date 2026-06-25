import peewee as pw

orm = wiz.model("portal/season/orm")
base = orm.base("base")


class Model(base):
    class Meta:
        db_table = "question_bank_candidate_evidence"

    id = pw.CharField(max_length=48, primary_key=True)
    candidate_id = pw.CharField(max_length=40, index=True)
    rag_input_id = pw.CharField(max_length=40, default="", index=True)
    source_chunk_id = pw.CharField(max_length=40, default="", index=True)
    source_file = pw.CharField(max_length=240, default="", index=True)
    source_path = pw.TextField(default="")
    page_or_slide = pw.IntegerField(default=0, index=True)
    content_sha256 = pw.CharField(max_length=64, default="", index=True)
    mapped_scope_id = pw.CharField(max_length=40, default="", index=True)
    mapping_confidence = pw.CharField(max_length=24, default="", index=True)
    evidence_role = pw.CharField(max_length=32, default="answer_evidence", index=True)
    created = pw.DateTimeField(index=True)
