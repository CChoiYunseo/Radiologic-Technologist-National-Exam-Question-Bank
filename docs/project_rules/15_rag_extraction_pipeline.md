# RAG Extraction Pipeline

전공 자료는 원문을 2차 PDF로 재작성하지 않고, 원문 파일에서 직접 텍스트를 추출한 뒤 chunk 단위 JSONL로 저장한다. MVP에서는 1·2교시 텍스트 문제 생성을 우선하므로 표, 그림, 도식, 장비 구조도 자동 이해는 별도 Phase로 분리한다.

## Installed Tools

- `poppler-utils`: `pdftotext`, `pdfinfo`, `pdftoppm`
- `ghostscript`: PDF 렌더링과 표 추출 보조
- `tesseract-ocr` + `kor`, `eng`: 한국어/영어 OCR
- `PyMuPDF`, `pdfplumber`, `pypdf`: PDF 텍스트와 레이아웃 추출
- `pytesseract`, `pdf2image`, `opencv-python-headless`: OCR fallback
- `camelot-py`: 표 추출 보강
- `python-pptx`, `python-docx`: PPTX/DOCX 추출

## Extraction Order

1. PDF 텍스트 레이어를 우선 추출한다.
2. 페이지 텍스트가 부족하면 Tesseract OCR을 적용한다.
3. 문서를 정제하고 chunk를 생성한다.
4. PPTX는 슬라이드 단위, DOCX는 문단 단위로 추출한다.
5. 각 chunk에 source file, page/slide, 추출 방식, 추출 품질, review 필요 여부를 저장한다.

## Phase 2 Tables And Visuals

- MVP 기본 실행에서는 표와 그림 자동 이해를 수행하지 않는다.
- 표: 행·열 관계가 중요하므로 OCR 텍스트가 아니라 구조화된 표 추출 결과를 우선한다.
- 그림/도식/장비 구조도: Vision LLM으로 설명문을 생성하되, 설명문은 원문 대체물이 아니라 RAG 검색 보조 메타데이터로 취급한다.
- 검수 전 Vision 설명문은 정답 근거로 단독 사용하지 않는다.
- 시각자료가 있는 chunk는 `image_count`, `visual_image_count`, `drawing_count`, `has_visual_elements`, `visual_element_count`, `visual_description_status`, `visual_review_priority`로 추적한다.
- PDF 선분/레이아웃 요소가 과잉 탐지될 수 있으므로 기본값은 페이지 면적 15% 이상 90% 이하 이미지가 있거나 drawing 수가 500개 이상인 페이지만 Vision 설명 후보로 표시한다.

## Output

기본 출력 위치:

```text
resources/extracted/subject_references/
├── documents.json
├── chunks.jsonl
└── extraction_report.json
```

## Command

```bash
python3 scripts/extract_subject_references.py
```

MVP 기본 실행은 텍스트 레이어 추출과 OCR만 수행한다.

스모크 테스트:

```bash
python3 scripts/extract_subject_references.py --max-pages 2 --output resources/extracted/subject_references_smoke
```

OCR이 너무 오래 걸리는 경우:

```bash
python3 scripts/extract_subject_references.py --no-ocr
```

Phase 2 표 추출을 켜는 경우:

```bash
python3 scripts/extract_subject_references.py --extract-tables
```

Phase 2 시각자료 후보 탐지를 켜는 경우:

```bash
python3 scripts/extract_subject_references.py --detect-visuals
```

## PaddleOCR Note

현재 기본 런타임은 Python 3.14이며, 이 환경에서는 `paddlepaddle` 배포본이 없어 PaddleOCR을 직접 사용할 수 없다. 필요한 경우 별도 Python 3.12 OCR 전용 가상환경을 만들어 PaddleOCR 계열을 분리 운용한다.
