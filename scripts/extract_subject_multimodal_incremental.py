#!/usr/bin/env python3
"""Incrementally extract table, formula, figure, and diagram candidates."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz
import pdfplumber

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from extract_subject_references_advanced import (
    clean_text,
    common_chunk_fields,
    extract_formula_chunks,
    extract_tables_for_page,
    extract_visual_chunks,
    material_folder,
    relative_to_project,
    selected_indices,
    split_text,
    stable_id,
    text_quality,
)


DEFAULT_MATERIALS = PROJECT_ROOT / "materials" / "04_subject_references"
DEFAULT_OUTPUT = PROJECT_ROOT / "resources" / "extracted" / "subject_references_multimodal_full_incremental"
DEFAULT_ASSET_OUTPUT = Path("/opt/app/extracted_assets/subject_references_multimodal_full_incremental")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def append_jsonl(handle, record: dict[str, Any]) -> None:
    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    handle.flush()


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


def load_existing_visual_counts(path: Path) -> Counter:
    counts: Counter = Counter()
    if not path.exists():
        return counts
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("chunk_type") in {"figure", "diagram"} and record.get("source_path"):
                counts[record["source_path"]] += 1
    return counts


def qwen_queue_record(chunk: dict[str, Any]) -> dict[str, Any]:
    structured = chunk.get("structured_content") or {}
    return {
        "queue_id": chunk.get("chunk_id"),
        "status": "pending_qwen3_vl",
        "chunk_type": chunk.get("chunk_type"),
        "source_file": chunk.get("source_file"),
        "source_path": chunk.get("source_path"),
        "page_or_slide": chunk.get("page_or_slide"),
        "source_image_path": chunk.get("source_image_path"),
        "caption": structured.get("caption", ""),
        "nearby_text": structured.get("nearby_text", ""),
        "embedded_text_candidates": structured.get("embedded_text_candidates", []),
        "multimodal_seed": structured.get("multimodal_seed", {}),
        "created_at": now_iso(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_MATERIALS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--asset-output", type=Path, default=DEFAULT_ASSET_OUTPUT)
    parser.add_argument("--chunk-chars", type=int, default=1800)
    parser.add_argument("--overlap-chars", type=int, default=180)
    parser.add_argument("--asset-dpi", type=int, default=180)
    parser.add_argument("--visual-image-min-area-ratio", type=float, default=0.15)
    parser.add_argument("--visual-image-max-area-ratio", type=float, default=0.9)
    parser.add_argument("--visual-drawing-threshold", type=int, default=500)
    parser.add_argument("--max-visual-assets-per-document", type=int, default=80)
    parser.add_argument("--limit-docs", type=int, default=0)
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument("--sample-pages", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    args.asset_output.mkdir(parents=True, exist_ok=True)

    documents_path = args.output / "documents.jsonl"
    pages_path = args.output / "pages.jsonl"
    chunks_path = args.output / "chunks_all.jsonl"
    review_path = args.output / "review_queue.jsonl"
    qwen_queue_path = args.output / "qwen3_vl_pending_queue.jsonl"
    report_path = args.output / "multimodal_full_report.json"

    completed = load_completed_documents(documents_path) if args.resume else set()
    completed_pages = load_completed_pages(pages_path) if args.resume else set()
    existing_visual_counts = load_existing_visual_counts(chunks_path) if args.resume else Counter()
    pdfs = sorted(args.input.rglob("*.pdf"))
    if args.limit_docs:
        pdfs = pdfs[: args.limit_docs]

    mode = "a" if args.resume else "w"
    started_at = now_iso()
    aggregate = Counter()
    errors: list[dict[str, Any]] = []
    processed_documents = 0
    processed_pages = 0
    processed_chunks = 0

    extraction_args = argparse.Namespace(
        input=args.input,
        output=args.output,
        asset_output=args.asset_output,
        chunk_chars=args.chunk_chars,
        overlap_chars=args.overlap_chars,
        ocr=False,
        ocr_min_chars=80,
        ocr_dpi=140,
        ocr_lang="kor+eng",
        ocr_timeout=10,
        asset_dpi=args.asset_dpi,
        visual_image_min_area_ratio=args.visual_image_min_area_ratio,
        visual_image_max_area_ratio=args.visual_image_max_area_ratio,
        visual_drawing_threshold=args.visual_drawing_threshold,
        max_visual_assets_per_document=args.max_visual_assets_per_document,
        max_pages=args.max_pages,
        sample_pages=args.sample_pages,
        extract_tables=True,
        extract_formulas=True,
        extract_visuals=True,
        save_crops=True,
    )

    with documents_path.open(mode, encoding="utf-8") as documents_file, \
        pages_path.open(mode, encoding="utf-8") as pages_file, \
        chunks_path.open(mode, encoding="utf-8") as chunks_file, \
        review_path.open(mode, encoding="utf-8") as review_file, \
        qwen_queue_path.open(mode, encoding="utf-8") as qwen_queue_file:
        for doc_index, pdf in enumerate(pdfs, start=1):
            source_path = relative_to_project(pdf)
            if source_path in completed:
                print(json.dumps({"skipped": doc_index, "source_file": pdf.name, "reason": "already_completed"}, ensure_ascii=False))
                continue
            try:
                document_id = stable_id(source_path, pdf.stat().st_size, pdf.stat().st_mtime_ns)
                fitz_doc = fitz.open(str(pdf))
                page_indices = selected_indices(len(fitz_doc), extraction_args)
                plumber_pdf = None
                try:
                    plumber_pdf = pdfplumber.open(str(pdf))
                except Exception:
                    plumber_pdf = None
                visual_count = int(existing_visual_counts.get(source_path, 0))
                doc_counter: Counter = Counter()
                doc_processed_pages = 0
                doc_processed_chunks = 0
                for page_index in page_indices:
                    page_number = page_index + 1
                    if (source_path, page_number) in completed_pages:
                        continue
                    page = fitz_doc[page_index]
                    text = clean_text(page.get_text("text", sort=True))
                    quality = text_quality(text, 0.92 if text else 0.0)
                    page_review_reason = "" if quality == "high" else f"text_quality_{quality}"
                    page_chunks: list[dict[str, Any]] = []
                    text_chunk_count = 0
                    for chunk_index, content in enumerate(split_text(text, args.chunk_chars, args.overlap_chars)):
                        chunk_id = stable_id(document_id, page_number, "text", chunk_index, content)
                        page_chunks.append(common_chunk_fields(
                            chunk_id=chunk_id,
                            document_id=document_id,
                            chunk_type="text",
                            source_file=pdf.name,
                            source_path=source_path,
                            page_or_slide=page_number,
                            bbox=[],
                            content=content,
                            structured_content={
                                "chunk_index": chunk_index,
                                "token_estimate": max(1, len(content) // 3),
                            },
                            source_image_path="",
                            extraction_method="pymupdf_text",
                            extraction_quality=quality,
                            confidence_score=0.92 if text else 0.0,
                            needs_review=quality != "high",
                            review_reason=page_review_reason,
                            created_at=now_iso(),
                        ))
                        text_chunk_count += 1

                    table_chunks: list[dict[str, Any]] = []
                    if plumber_pdf and page_index < len(plumber_pdf.pages):
                        table_chunks = extract_tables_for_page(
                            pdf_path=pdf,
                            fitz_doc=fitz_doc,
                            pdfplumber_page=plumber_pdf.pages[page_index],
                            page_number=page_number,
                            document_id=document_id,
                            asset_dir=args.asset_output,
                            created_at=now_iso(),
                            render_dpi=args.asset_dpi,
                            save_crops=True,
                        )
                        page_chunks.extend(table_chunks)

                    formula_chunks = extract_formula_chunks(
                        pdf_path=pdf,
                        page=page,
                        page_number=page_number,
                        document_id=document_id,
                        asset_dir=args.asset_output,
                        created_at=now_iso(),
                        render_dpi=args.asset_dpi,
                        save_crops=True,
                    )
                    page_chunks.extend(formula_chunks)

                    visual_chunks, visual_count = extract_visual_chunks(
                        pdf_path=pdf,
                        page=page,
                        page_number=page_number,
                        document_id=document_id,
                        asset_dir=args.asset_output,
                        created_at=now_iso(),
                        render_dpi=args.asset_dpi,
                        min_area_ratio=args.visual_image_min_area_ratio,
                        max_area_ratio=args.visual_image_max_area_ratio,
                        drawing_threshold=args.visual_drawing_threshold,
                        save_crops=True,
                        max_visual_assets=args.max_visual_assets_per_document,
                        current_visual_count=visual_count,
                    )
                    page_chunks.extend(visual_chunks)

                    for chunk in page_chunks:
                        append_jsonl(chunks_file, chunk)
                        aggregate[f"chunk_{chunk.get('chunk_type')}"] += 1
                        aggregate[f"quality_{chunk.get('extraction_quality')}"] += 1
                        doc_counter[f"chunk_{chunk.get('chunk_type')}"] += 1
                        if chunk.get("needs_review"):
                            append_jsonl(review_file, chunk)
                        if chunk.get("chunk_type") in {"table", "formula", "figure", "diagram"}:
                            append_jsonl(qwen_queue_file, qwen_queue_record(chunk))

                    page_record = {
                        "document_id": document_id,
                        "source_file": pdf.name,
                        "source_path": source_path,
                        "page_or_slide": page_number,
                        "text_chars": len(text),
                        "text_quality": quality,
                        "text_chunk_count": text_chunk_count,
                        "table_chunk_count": len(table_chunks),
                        "formula_chunk_count": len(formula_chunks),
                        "visual_chunk_count": len(visual_chunks),
                        "extraction_method": "pymupdf_text",
                        "ocr_confidence": None,
                        "ocr_note": "",
                    }
                    append_jsonl(pages_file, page_record)
                    completed_pages.add((source_path, page_number))
                    processed_pages += 1
                    processed_chunks += len(page_chunks)
                    doc_processed_pages += 1
                    doc_processed_chunks += len(page_chunks)
                    if page_number % 25 == 0 or page_number == page_indices[-1] + 1:
                        print(json.dumps({
                            "source_file": pdf.name,
                            "doc": f"{doc_index}/{len(pdfs)}",
                            "page": f"{page_number}/{len(fitz_doc)}",
                            "chunks_total_this_run": processed_chunks,
                        }, ensure_ascii=False))
                if plumber_pdf:
                    plumber_pdf.close()
                fitz_doc.close()
                expected_pages = len(page_indices)
                completed_for_doc = sum(1 for key in completed_pages if key[0] == source_path)
                if completed_for_doc >= expected_pages:
                    document = {
                        "document_id": document_id,
                        "source_file": pdf.name,
                        "source_path": source_path,
                        "source_type": "pdf",
                        "material_folder": material_folder(pdf, args.input),
                        "file_size": pdf.stat().st_size,
                        "page_count": expected_pages,
                        "created_at": now_iso(),
                        "status": "completed",
                        "doc_index": doc_index,
                    }
                    append_jsonl(documents_file, document)
                    processed_documents += 1
                print(json.dumps({
                    "completed_doc": doc_index,
                    "total_docs": len(pdfs),
                    "source_file": pdf.name,
                    "pages": doc_processed_pages,
                    "chunks": doc_processed_chunks,
                    "chunk_type_counts": dict(doc_counter),
                }, ensure_ascii=False))
            except Exception as exc:
                error = {"source_file": pdf.name, "source_path": source_path, "error": f"{type(exc).__name__}: {exc}", "created_at": now_iso()}
                errors.append(error)
                append_jsonl(documents_file, {**error, "status": "error", "doc_index": doc_index})
                print(json.dumps(error, ensure_ascii=False))

    report = {
        "created_at": now_iso(),
        "started_at": started_at,
        "input": str(args.input),
        "output": str(args.output),
        "asset_output": str(args.asset_output),
        "target_document_count": len(pdfs),
        "processed_document_count": processed_documents,
        "processed_page_count": processed_pages,
        "processed_chunk_count": processed_chunks,
        "counts": dict(aggregate),
        "errors": errors,
        "notes": [
            "OCR is not performed here; OCR text is produced by extract_subject_ocr_full_incremental.py.",
            "Tables, formulas, figures, and diagrams are review/Qwen3-VL pending by default.",
            "Source crops are preserved under /opt/app/extracted_assets.",
        ],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
