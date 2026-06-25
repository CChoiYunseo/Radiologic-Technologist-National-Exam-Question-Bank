# Radiologic Technologist National Exam Question Bank

This repository contains an implementation of an evidence-guided question bank service for the Korean Radiologic Technologist National Exam. The current implementation focuses on text-based first- and second-period exam content. Practical image-based third-period questions are treated as a later expansion because they require a separate medical image asset, generation, and validation workflow.

The service is not designed to copy source textbooks or past questions. It is designed to structure exam scope data, retrieve locally indexed subject evidence, generate five-option multiple-choice candidates, validate them, and route them through expert review before they are used for practice.

## Current Implementation

The codebase is built on the WIZ sample project structure with Python backend modules and Angular/WIZ page components.

Implemented service areas:

- Exam scope management for period, subject, field, area, and detail-level scope rows
- RAG evidence preview from a local subject-reference vector index
- Generation request creation for selected scope, difficulty, question type, focus, and evidence count
- LLM-backed question generation through OpenAI-compatible or Gemini-compatible API settings
- Rule-based validation harness for format, metadata, scope, evidence, duplicate, language, and copyright checks
- Question bank candidate storage with evidence and validation metadata
- Expert review dashboard for filtering, inspecting, approving, rejecting, and revising generated candidates
- Practice page that serves approved candidates, or pending candidates as preview content when no approved candidates exist

## Repository Safety

Private source materials are intentionally excluded from version control.

The following local source folders are ignored and should not be uploaded:

- `materials/01_question_guidelines/`
- `materials/02_exam_scope/`
- `materials/03_item_design/`
- `materials/04_subject_references/`

These folders may contain PDFs, textbooks, official reference documents, instructor-provided files, OCR inputs, or other privately owned materials used only for local extraction and indexing. Keep them outside Git, or use a private storage workflow if they need to be shared.

The following local output folders are also ignored because they can contain OCR text, page render images, source chunks, embeddings, prompts, generated drafts, or evidence excerpts derived from private materials:

- `data/`
- `resources/extracted/`
- `resources/extracted_assets/`
- `resources/generated/`
- `resources/interpreted/`
- `resources/reports/`
- `resources/vector_db/`

The code and reusable rule assets are safe to publish. Private source material and source-derived local artifacts are not.

## Architecture

```text
Local source materials (not committed)
        |
        v
Extraction and normalization scripts
        |
        v
Rules, scope data, chunks, reports, and vector indexes
        |
        v
RAG search for evidence by exam scope
        |
        v
LLM question generation
        |
        v
Validation harness and expert review
        |
        v
Question bank candidate store
        |
        v
Admin review and practice pages
```

## Main Application Pages

| Route | Purpose |
| --- | --- |
| `/home` | Public practice landing page |
| `/practice` | Candidate-based question solving flow |
| `/admin/exam-scope` | Exam scope browsing, DB seeding, RAG preview, and generation request creation |
| `/admin/question-candidates` | Expert review queue for generated candidates |
| `/access` | Expert/admin login |

The default route points to `/home`. The sidebar is configured for the admin workflow and links to exam scope and question candidate review screens.

## Backend Modules

Core domain access is exposed through `src/model/struct.py`.

Important structs:

- `exam_scope`: Loads rule-based exam scope data, validates selected scope rows, and seeds the `exam_scope` table.
- `rag`: Searches the local subject-reference vector index and builds generation payloads with evidence chunks.
- `question_generation_request`: Stores generation requests and runs generation for selected scopes.
- `question_generator`: Builds LLM prompts, calls OpenAI-compatible or Gemini APIs, normalizes generated JSON, and validates output.
- `validation`: Applies the rule-based harness to generated questions.
- `question_bank_candidate`: Provides question candidate data access for review and practice workflows.
- `rules`: Loads JSON rulebooks from `resources/rules`.

Important database models:

- `exam_scope`
- `source_document`
- `source_chunk`
- `question_generation_request`
- `question_bank_candidate`
- `question_bank_candidate_evidence`
- `question_bank_candidate_validation`

## Pipeline Scripts

The `scripts/` directory contains local utilities used to build the question-generation assets and candidate store. Their outputs are intentionally ignored by Git because they are derived from private materials.

Key stages:

- Extract and normalize material rules: `extract_material_rules.py`, `enrich_material_rules.py`, `rebuild_generation_rules.py`
- Extract text, OCR, multimodal, and visual assets: `extract_subject_references.py`, `extract_subject_references_advanced.py`, `extract_subject_ocr_full_incremental.py`, `extract_subject_multimodal_incremental.py`, `extract_subject_phase2_assets.py`
- Map extracted chunks to exam scope: `map_subject_chunks_to_scope.py`, `build_rag_index_input_dataset.py`, `map_rag_index_input_to_scope.py`
- Build retrieval indexes: `build_subject_vector_db.py`, `build_rag_text_bm25_index.py`
- Build request packages and review candidates: `build_question_request_packages.py`, `build_review_candidate_index.py`, `build_question_bank_candidate_store.py`
- Generate and validate drafts: `generate_question_dry_run.py`, `generate_question_batch.py`, `validate_rule_based_generation_harness.py`
- Search and smoke test retrieval: `search_subject_vector_db.py`, `search_rag_text_bm25_index.py`, `run_rag_search_smoke_tests.py`

These scripts assume the private local material folders exist on the developer machine. They rebuild ignored local artifacts such as extracted chunks, vector indexes, reports, and generated candidate stores.

## Rule Assets

Public, reusable rule and schema assets are stored under `resources/rules/`.

Examples:

- `exam_scope.json`: structured exam scope source for scope selection and validation
- `learning_objectives.json`: learning objective mapping
- `generation_policy.json`: generation constraints
- `question_schema.json`: expected question structure
- `validation_harness_spec.json`: validation harness definition
- `copyright_policy.json`: source-use and similarity constraints
- `question_language_rulebook.json`: Korean item-writing language rules
- `item_design_rulebook.json`: item design rules for exam-style questions

## LLM Configuration

Question generation can use either a Gemini-compatible setup or an OpenAI-compatible chat completions endpoint.

OpenAI-compatible environment variables:

```bash
OPENAI_API_KEY=...
OPENAI_API_BASE=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
```

Generic OpenAI-compatible variables:

```bash
LLM_PROVIDER=openai_compatible
LLM_API_URL=https://example.com/v1/chat/completions
LLM_API_KEY=...
LLM_MODEL=...
```

Gemini-compatible variables:

```bash
LLM_PROVIDER=gemini
GOOGLE_API_KEY=...
LLM_MODEL=gemini-3.5-flash
```

The generator requires JSON output and normalizes generated questions before validation.

## Validation Harness

Generated candidates are checked before storage or review.

Current validation coverage includes:

- Exactly five options
- Exactly one answer
- Required explanation
- Required learning objective or target
- Valid exam scope and MVP exclusion for practical image-based content
- Required metadata such as period, subject, field, area, difficulty, question type, and evidence references
- Registered question type
- Evidence reference presence
- Law metadata for law-type questions
- Duplicate options and duplicate question candidates
- Unsupported numbers, units, and formulas not found in source chunks
- Korean item-writing language warnings
- Copyright and source similarity checks

The harness returns pass/fail status, grouped findings, agent-style reports, final judge output, and storage readiness metadata.

## Candidate Review and Practice Flow

Generated items are stored as candidates instead of being published immediately. The candidate store is a local generated artifact and is not committed by default.

Candidate statuses include:

- `pending_expert_review`
- `needs_revision`
- `expert_rejected`
- `expert_passed`
- `expert_approved`

The admin review page reads from the local candidate SQLite store and supports status updates, review notes, filtering, detail views, evidence inspection, and validation result inspection.

The practice page uses `expert_passed` candidates when available. If there are no passed candidates, it falls back to `pending_expert_review` candidates as preview content so the UI can be tested before final expert approval.

## Local Development Notes

The repository is a WIZ/Angular project. The current app depends on the WIZ runtime layout and generated Angular build structure used by the original sample project.

Useful checks:

```bash
python -m py_compile $(find scripts src/model src/app/page.admin.exam_scope src/app/page.admin.question_candidates src/app/page.practice -name '*.py' -print)
npm --prefix build run build
```

The Python compile check validates backend syntax. The Angular build may require the complete WIZ build environment and existing style imports used by the sample project.

## Implemented MVP Scope

The implementation follows the project plan with these practical MVP boundaries:

- Target exam area: first- and second-period text-based questions
- Question format: Korean five-option multiple-choice questions
- Generation method: RAG evidence payload plus LLM JSON generation
- Validation method: rule-based harness plus review metadata
- Review method: expert approval workflow before practice publication
- Excluded from MVP: third-period image/practical questions and direct publication without expert review

## Korean Summary

이 저장소는 방사선사 국가고시 문제은행 서비스를 구현하기 위한 코드 저장소입니다. 현재 구현은 1·2교시 텍스트 기반 5지선다형 문항 생성을 MVP 범위로 잡고 있으며, 3교시 영상 기반 실기 문항은 별도 확장 단계로 제외했습니다.

구현된 핵심 기능은 출제범위 관리, 로컬 RAG 근거 검색, LLM 기반 문항 생성 요청, 검증 harness, 전문가 검수 후보 저장소, 관리자 검수 화면, 사용자 문제 풀이 화면입니다. 생성 문항은 바로 공개하지 않고 `pending_expert_review` 상태로 저장한 뒤 전문가가 승인해야 연습 문제로 사용되는 흐름입니다.

전공자료 원본은 개인 소유물이므로 Git에 올리지 않습니다. 특히 `materials/01_question_guidelines/`, `materials/02_exam_scope/`, `materials/03_item_design/`, `materials/04_subject_references/` 폴더는 `.gitignore`로 제외했습니다. PDF, 교재, 교수 제공 자료, OCR 입력물 등 원자료는 로컬에서만 사용해야 합니다.

코드는 공개 가능한 서비스 구현과 파이프라인 중심으로 구성되어 있고, 실제 자료 추출·벡터DB 구축·문항 생성은 개발자 로컬 자료와 환경 변수를 사용해 실행하는 구조입니다.
