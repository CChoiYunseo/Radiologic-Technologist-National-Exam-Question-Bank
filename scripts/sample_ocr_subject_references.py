#!/usr/bin/env python3
"""Create incremental OCR samples for scanned subject-reference PDFs."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from extract_subject_references_advanced import clean_text, ocr_pdf_page, selected_indices, text_quality


DEFAULT_MATERIALS = PROJECT_ROOT / "materials" / "04_subject_references"
DEFAULT_TEXT_LAYER = PROJECT_ROOT / "resources" / "extracted" / "subject_references_text_layer_full"
DEFAULT_OUTPUT = PROJECT_ROOT / "resources" / "extracted" / "subject_references_ocr_sample_incremental"


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


def should_sample_ocr(pdf: Path, text_summary: dict[str, dict[str, Any]], include_text_ok: bool) -> bool:
    if include_text_ok:
        return True
    row = text_summary.get(pdf.name)
    if not row:
        return True
    page_count = max(1, int(row.get("pages") or 1))
    high_ratio = float(row.get("high") or 0) / page_count
    return high_ratio < 0.15


def page_indices_for(total_count: int, sample_pages: int) -> list[int]:
    class Args:
        max_pages = 0
        def __init__(self, sample_pages: int):
            self.sample_pages = sample_pages
    return selected_indices(total_count, Args(sample_pages))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_MATERIALS)
    parser.add_argument("--text-layer-dir", type=Path, default=DEFAULT_TEXT_LAYER)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--sample-pages", type=int, default=2)
    parser.add_argument("--ocr-dpi", type=int, default=140)
    parser.add_argument("--ocr-timeout", type=int, default=10)
    parser.add_argument("--ocr-lang", default="kor+eng")
    parser.add_argument("--include-text-ok", action="store_true")
    parser.add_argument("--limit-docs", type=int, default=0)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    pages_out = args.output / "ocr_sample_pages.jsonl"
    docs_out = args.output / "ocr_sample_documents.jsonl"
    report_out = args.output / "ocr_sample_report.json"

    text_summary = read_text_layer_summary(args.text_layer_dir)
    pdfs = sorted(path for path in args.input.rglob("*.pdf") if should_sample_ocr(path, text_summary, args.include_text_ok))
    if args.limit_docs:
        pdfs = pdfs[: args.limit_docs]

    page_records: list[dict[str, Any]] = []
    doc_records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    pages_file = pages_out.open("w", encoding="utf-8")
    docs_file = docs_out.open("w", encoding="utf-8")
    try:
        for doc_index, pdf in enumerate(pdfs, start=1):
            started_at = now_iso()
            try:
                doc = fitz.open(str(pdf))
                indices = page_indices_for(len(doc), args.sample_pages)
                qualities = []
                chars = []
                for index in indices:
                    page = doc[index]
                    page_number = index + 1
                    text, confidence, note = ocr_pdf_page(page, args.ocr_dpi, args.ocr_lang, args.ocr_timeout)
                    quality = text_quality(text, confidence)
                    record = {
                        "source_file": pdf.name,
                        "source_path": str(pdf),
                        "material_folder": str(pdf.parent.relative_to(args.input)),
                        "page_or_slide": page_number,
                        "sample_index": index,
                        "ocr_dpi": args.ocr_dpi,
                        "ocr_confidence": confidence,
                        "extraction_quality": quality,
                        "text_length": len(clean_text(text)),
                        "note": note,
                        "content_preview": clean_text(text)[:1200],
                        "created_at": now_iso(),
                    }
                    pages_file.write(json.dumps(record, ensure_ascii=False) + "\n")
                    pages_file.flush()
                    page_records.append(record)
                    qualities.append(quality)
                    chars.append(record["text_length"])
                doc_record = {
                    "source_file": pdf.name,
                    "source_path": str(pdf),
                    "material_folder": str(pdf.parent.relative_to(args.input)),
                    "doc_index": doc_index,
                    "page_count": len(doc),
                    "sampled_pages": [index + 1 for index in indices],
                    "sample_count": len(indices),
                    "quality_counts": {quality: qualities.count(quality) for quality in sorted(set(qualities))},
                    "avg_text_length": round(sum(chars) / len(chars), 1) if chars else 0,
                    "started_at": started_at,
                    "finished_at": now_iso(),
                }
                docs_file.write(json.dumps(doc_record, ensure_ascii=False) + "\n")
                docs_file.flush()
                doc_records.append(doc_record)
                print(json.dumps({
                    "done": doc_index,
                    "total": len(pdfs),
                    "source_file": pdf.name,
                    "sampled_pages": doc_record["sampled_pages"],
                    "quality_counts": doc_record["quality_counts"],
                    "avg_text_length": doc_record["avg_text_length"],
                }, ensure_ascii=False))
            except Exception as exc:
                error = {"source_file": pdf.name, "source_path": str(pdf), "error": str(exc), "created_at": now_iso()}
                errors.append(error)
                print(json.dumps(error, ensure_ascii=False))
    finally:
        pages_file.close()
        docs_file.close()

    report = {
        "created_at": now_iso(),
        "input": str(args.input),
        "text_layer_dir": str(args.text_layer_dir),
        "output": str(args.output),
        "target_document_count": len(pdfs),
        "sampled_document_count": len(doc_records),
        "sampled_page_count": len(page_records),
        "sample_pages": args.sample_pages,
        "ocr_dpi": args.ocr_dpi,
        "ocr_timeout": args.ocr_timeout,
        "errors": errors,
    }
    report_out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
