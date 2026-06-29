# Current Implementation Status

This note summarizes the implementation state after the latest pipeline and service updates. It tracks code-level behavior only; private study materials and generated local artifacts remain outside version control.

## Generation Pipeline Updates

- Added semantic Knowledge Object builders for v1/v2 generation planning.
- Added folder-authoritative scope mapping and semantic curation steps to reduce scope drift before package generation.
- Added safe generation package rebuild, expansion, and shortage coverage audit utilities.
- Added pilot package builders for text-question generation over reviewed semantic inputs.
- Added LLM secondary feedback analysis and feedback application scripts so revise/reject reasons can feed back into curated Knowledge Objects and request packages.
- Added an import path for LLM-pass question drafts into the expert-review candidate store.

## Validation Schema Updates

The generated question output schema now includes `llm_first_check`.

The first-check object records:

- overall verdict: `pass`, `revise`, or `reject`
- scope alignment
- learning objective alignment
- evidence grounding
- answer uniqueness
- option quality
- explanation quality
- copyright safety
- text-only policy
- notes

This metadata complements the rule-based harness. It is not a replacement for expert review.

## Candidate Store Updates

Candidate import and storage scripts now preserve additional validation metadata and can distinguish new source stages such as `subject_quota_llm_pass`.

The admin candidate page can display:

- generation source stage labels
- validation details
- evidence metadata
- linked visual asset metadata for structured visual candidates
- simplified SVG previews when a locally generated educational diagram is available

SVG previews are generated from structured visual package metadata. They are not embedded copies of source images.

## Practice Flow Updates

The practice API now supports assembled exam papers for the first and second periods.

Implemented behavior:

- load an assembled exam by period
- return exam metadata, subject counts, and shortage warnings
- use deterministic per-session option shuffling
- grade submitted display choices by mapping them back to the original answer index
- keep randomization stable within the same practice session

This allows realistic practice without changing the stored candidate answer data.

## Exam Assembly

The exam assembly script builds strict text-only exam papers from usable candidate statuses and excludes visual draft candidates from text-only exam papers.

The assembly flow records:

- period target count
- selected question count
- subject target distribution
- subject selected counts
- completion status
- shortage warnings

Shortage outputs are local worklists used to guide additional text-question generation.

## Answer Position Rebalancing

Answer-position tooling was added to reduce positional bias in generated candidates.

The rebalancing script:

- updates only option order and answer index
- preserves stems, explanations, evidence, and validation records
- writes an audit report locally
- avoids rewriting items whose explanation directly references option numbers

The practice page also shuffles options per session, so stored answer order and displayed answer order are intentionally decoupled.

## Visual Candidate Handling

The visual SVG asset builder creates simplified educational diagrams from structured package metadata such as caption, labels, nearby summary, and embedded text candidates.

Policy constraints:

- source images are not embedded
- SVG diagrams are newly authored educational diagrams
- visual candidates remain subject to expert review
- text-only exam papers exclude visual draft candidates

## Documentation Impact

README now reflects the current MVP:

- semantic Knowledge Object and safe package planning
- LLM-first validation metadata
- candidate review with visual diagram preview support
- assembled first- and second-period practice exams
- deterministic option randomization

Generated data, reports, indexes, candidate stores, and extracted source-derived artifacts remain local-only.
