#!/usr/bin/env python3
"""Incrementally OCR scanned subject-reference PDFs into reviewable chunks."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from extract_subject_references_advanced import clean_text, ocr_pdf_page, split_text, stable_id, text_quality


DEFAULT_MATERIALS = PROJECT_ROOT / "materials" / "04_subject_references"
DEFAULT_TEXT_LAYER = PROJECT_ROOT / "resources" / "extracted" / "subject_references_text_layer_full_v2"
DEFAULT_OUTPUT = PROJECT_ROOT / "resources" / "extracted" / "subject_references_ocr_full_incremental"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_text_layer_summary(path: Path) -> dict[str, dict[str, Any]]:
    pages_path = path / "pages.json"
    if not pages_path.exists():
        return {}
    pages = json.loads(pages_path.read_text(encoding="utf-8"))
    summary: dict[str, dict[str, Any]] = defaultdict(lambda: {"pages": 0, "high": 0, "medium": 0, "low": 0})
    for page in pages:
        source = page.get("source_file", "")
        quality = page.get("text_quality") or "low"
        summary[source]["pages"] += 1
        summary[source][quality] += 1
    return dict(summary)


def should_ocr(pdf: Path, text_summary: dict[str, dict[str, Any]], include_text_ok: bool) -> bool:
    if include_text_ok:
        return True
    row = text_summary.get(pdf.name)
    if not row:
        return True
    page_count = max(1, int(row.get("pages") or 1))
    high_ratio = float(row.get("high") or 0) / page_count
    return high_ratio < 0.15


def load_completed_documents(path: Path) -> set[str]:
    completed: set[str] = set()
    if not path.exists():
        return completed
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("status") == "completed" and record.get("source_path"):
                completed.add(record["source_path"])
    return completed


def load_completed_pages(path: Path) -> set[tuple[str, int]]:
    completed: set[tuple[str, int]] = set()
    if not path.exists():
        return completed
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            source_path = record.get("source_path")
            page = record.get("page_or_slide")
            if source_path and isinstance(page, int):
                completed.add((source_path, page))
    return completed


def classify_page(page_number: int, text: str, quality: str) -> tuple[bool, str]:
    content = clean_text(text)
    if len(content) < 40:
        return True, "blank_or_too_short"
    front_matter_terms = ("목차", "차례", "판권", "ISBN", "저자", "발행", "머리말", "서문")
    if page_number <= 8 and any(term in content for term in front_matter_terms):
        return True, "front_matter_candidate"
    if quality == "low":
        return True, "low_ocr_quality"
    return False, ""


def append_jsonl(handle, record: dict[str, Any]) -> None:
    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    handle.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_MATERIALS)
    parser.add_argument("--text-layer-dir", type=Path, default=DEFAULT_TEXT_LAYER)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--ocr-dpi", type=int, default=140)
    parser.add_argument("--ocr-timeout", type=int, default=10)
    parser.add_argument("--ocr-lang", default="kor+eng")
    parser.add_argument("--chunk-chars", type=int, default=1800)
    parser.add_argument("--overlap-chars", type=int, default=180)
    parser.add_argument("--include-text-ok", action="store_true")
    parser.add_argument("--limit-docs", type=int, default=0)
    parser.add_argument("--max-pages-per-doc", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    pages_path = args.output / "ocr_pages.jsonl"
    chunks_path = args.output / "ocr_chunks_all.jsonl"
    high_chunks_path = args.output / "ocr_chunks_high.jsonl"
    review_path = args.output / "ocr_review_queue.jsonl"
    docs_path = args.output / "ocr_documents.jsonl"
    report_path = args.output / "ocr_full_report.json"

    completed = load_completed_documents(docs_path) if args.resume else set()
    completed_pages = load_completed_pages(pages_path) if args.resume else set()
    text_summary = read_text_layer_summary(args.text_layer_dir)
    pdfs = [path for path in sorted(args.input.rglob("*.pdf")) if should_ocr(path, text_summary, args.include_text_ok)]
    if args.limit_docs:
        pdfs = pdfs[: args.limit_docs]

    mode = "a" if args.resume else "w"
    started_at = now_iso()
    aggregate = Counter()
    errors: list[dict[str, Any]] = []
    processed_docs = 0
    processed_pages = 0
    processed_chunks = 0

    with pages_path.open(mode, encoding="utf-8") as pages_file, \
        chunks_path.open(mode, encoding="utf-8") as chunks_file, \
        high_chunks_path.open(mode, encoding="utf-8") as high_chunks_file, \
        review_path.open(mode, encoding="utf-8") as review_file, \
        docs_path.open(mode, encoding="utf-8") as docs_file:
        for doc_index, pdf in enumerate(pdfs, start=1):
            source_path = str(pdf)
            if source_path in completed:
                print(json.dumps({"skipped": doc_index, "source_file": pdf.name, "reason": "already_completed"}, ensure_ascii=False))
                continue
            doc_started_at = now_iso()
            doc_counter = Counter()
            doc_chunk_count = 0
            try:
                doc = fitz.open(str(pdf))
                total_pages = doc.page_count
                page_total = min(total_pages, args.max_pages_per_doc or total_pages)
                document_id = stable_id(source_path, total_pages, pdf.stat().st_size)
                for page_index in range(page_total):
                    page_number = page_index + 1
                    if (source_path, page_number) in completed_pages:
                        doc_counter["page_skipped_resume"] += 1
                        continue
                    text, confidence, note = ocr_pdf_page(doc[page_index], args.ocr_dpi, args.ocr_lang, args.ocr_timeout)
                    content = clean_text(text)
                    quality = text_quality(content, confidence)
                    exclude_candidate, exclude_reason = classify_page(page_number, content, quality)
                    page_record = {
                        "document_id": document_id,
                        "source_file": pdf.name,
                        "source_path": source_path,
                        "material_folder": str(pdf.parent.relative_to(args.input)),
                        "page_or_slide": page_number,
                        "text_length": len(content),
                        "ocr_confidence": confidence,
                        "extraction_quality": quality,
                        "exclude_candidate": exclude_candidate,
                        "exclude_reason": exclude_reason,
                        "note": note,
                        "content_preview": content[:800],
                        "created_at": now_iso(),
                    }
                    append_jsonl(pages_file, page_record)
                    doc_counter[f"page_{quality}"] += 1
                    doc_counter["page_excluded" if exclude_candidate else "page_body_candidate"] += 1
                    aggregate[f"page_{quality}"] += 1
                    aggregate["page_excluded" if exclude_candidate else "page_body_candidate"] += 1
                    processed_pages += 1

                    for chunk_index, chunk_text in enumerate(split_text(content, args.chunk_chars, args.overlap_chars), start=1):
                        chunk_quality = text_quality(chunk_text, confidence)
                        needs_review = exclude_candidate or chunk_quality != "high"
                        review_reason = exclude_reason if exclude_candidate else ("" if chunk_quality == "high" else f"{chunk_quality}_ocr_quality")
                        chunk_id = stable_id(document_id, page_number, "ocr_text", chunk_index, chunk_text[:160])
                        chunk_record = {
                            "chunk_id": chunk_id,
                            "document_id": document_id,
                            "chunk_type": "text",
                            "source_file": pdf.name,
                            "source_path": source_path,
                            "material_folder": str(pdf.parent.relative_to(args.input)),
                            "page_or_slide": page_number,
                            "content": chunk_text,
                            "extraction_method": "tesseract_ocr",
                            "extraction_quality": chunk_quality,
                            "confidence_score": round(float(confidence or 0), 4),
                            "needs_review": needs_review,
                            "review_reason": review_reason,
                            "pre_mapping_generation_candidate": bool(chunk_quality == "high" and not exclude_candidate),
                            "created_at": now_iso(),
                        }
                        append_jsonl(chunks_file, chunk_record)
                        if chunk_record["pre_mapping_generation_candidate"]:
                            append_jsonl(high_chunks_file, chunk_record)
                        if needs_review:
                            append_jsonl(review_file, chunk_record)
                        doc_chunk_count += 1
                        processed_chunks += 1
                        doc_counter[f"chunk_{chunk_quality}"] += 1
                        aggregate[f"chunk_{chunk_quality}"] += 1

                    if page_number % 25 == 0 or page_number == page_total:
                        print(json.dumps({
                            "source_file": pdf.name,
                            "doc": f"{doc_index}/{len(pdfs)}",
                            "page": f"{page_number}/{page_total}",
                            "pages_done_total": processed_pages,
                            "chunks_done_total": processed_chunks,
                        }, ensure_ascii=False))
                completed_in_previous_run = doc_counter["page_skipped_resume"]
                completed_in_this_run = sum(value for key, value in doc_counter.items() if key.startswith("page_") and key != "page_skipped_resume")
                doc_status = "completed" if completed_in_previous_run + completed_in_this_run >= page_total else "partial"
                doc_record = {
                    "source_file": pdf.name,
                    "source_path": source_path,
                    "material_folder": str(pdf.parent.relative_to(args.input)),
                    "status": doc_status,
                    "doc_index": doc_index,
                    "page_count": total_pages,
                    "processed_page_count": page_total,
                    "chunk_count": doc_chunk_count,
                    "counts": dict(doc_counter),
                    "started_at": doc_started_at,
                    "finished_at": now_iso(),
                }
                append_jsonl(docs_file, doc_record)
                processed_docs += 1
                print(json.dumps({
                    "completed_doc": doc_index,
                    "total_docs": len(pdfs),
                    "source_file": pdf.name,
                    "processed_pages": page_total,
                    "chunks": doc_chunk_count,
                    "counts": dict(doc_counter),
                }, ensure_ascii=False))
            except Exception as exc:
                error = {"source_file": pdf.name, "source_path": source_path, "error": str(exc), "created_at": now_iso()}
                errors.append(error)
                append_jsonl(docs_file, {**error, "status": "error", "doc_index": doc_index})
                print(json.dumps(error, ensure_ascii=False))

    report = {
        "created_at": now_iso(),
        "started_at": started_at,
        "input": str(args.input),
        "text_layer_dir": str(args.text_layer_dir),
        "output": str(args.output),
        "target_document_count": len(pdfs),
        "processed_document_count": processed_docs,
        "processed_page_count": processed_pages,
        "processed_chunk_count": processed_chunks,
        "ocr_dpi": args.ocr_dpi,
        "ocr_timeout": args.ocr_timeout,
        "counts": dict(aggregate),
        "errors": errors,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
