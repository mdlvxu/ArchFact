# Changelog

[English](./CHANGELOG.md) | [中文](./CHANGELOG中文.md)

## quality-baseline-v5 — 2026-09-20

Main changes since `quality-baseline-v4` (2026-09-07).

### PaddleOCR 3.x with PP-OCRv6_small

- Default OCR is PaddleOCR 3.7 (`ppocr3`) plus `PP-OCRv6_small`; download via BOS into `models/paddleocr` (weights stay gitignored)
- Hardware auto-tune: small/tiny/v4 can use up to 8 OCR workers; medium/server/v5 stay capped at 2 to avoid CPU OOM
- Worker ready handshake so model load is not counted against the page timeout (default 180s); timeout retry downscales `max_side` 1600 → 960
- Page OCR cache keys on provider/model/version/text/blocks; timeout and worker counts no longer bust the cache
- Number regions reuse page OCR; leftover crop OCR runs in parallel with a 20s region timeout

### Extraction robustness and UI

- Truncated LLM JSON is repaired or bisected (5054 / 5022) instead of failing the whole page
- The extract button stays disabled while a job is running or stopping and shows progress percent

### Publish layout and dead code

- Root `.gitignore` whitelist publishes launcher scripts and bilingual changelogs
- Remove unused frontend modules (`DocumentSheet`, `ExtractionResults`, `api/modules/user`, `stores/app`)
- Extraction routes no longer re-export application helpers; tests import from application/domain
- SETUP_WINDOWS documents `ppocr3` + `PP-OCRv6_small` as the recommended OCR path; 2.9 remains a fallback

### Tests

- Frontend: `pnpm test:run` 83 passed, 1 skipped
- Backend: `pytest` 213 passed
- Updated coverage for OCR ready handshake, bundled model dirs, hardware worker caps, and JSON salvage

---

## quality-baseline-v4 — 2026-09-07

Main changes since `quality-baseline-v3` (2026-08-13).

### Large-report import and job resume

- Default PDF upload cap is 512 MB; the Vite proxy and client timeouts scale with file size
- The page navigator shows upload / save / parse progress instead of freezing on `arrayBuffer()`
- Multipart uploads no longer send a boundary-less `Content-Type`, and PDF.js blob URLs stay alive until the document is released
- FastAPI startup resumes interrupted extraction jobs and skips pages that already finished
- Stale rematch, AI verification, and quality-evaluation runs are marked failed after process restart

### Hardware auto-tune and MongoDB

- `HARDWARE_AUTO_TUNE=true` (default) sets OCR workers, discovery concurrency, and page-batch size from the machine
- Unique `documents.sha256` reuses an already-stored PDF instead of writing GridFS twice
- Compound indexes plus TTL on `job_events` (60d) and semantic cache (90d)
- Unique partial index for one active verification session per job

### Restore the job that is still running

- `GET /extraction-jobs/recent/latest?include_active=true` prefers queued / extracting jobs over the latest completed one
- After refresh, the data-extraction page attaches to the in-progress report instead of an older completed PDF left in `localStorage`
- The page-navigator list fills the leftover column height so the last thumbnail and page label are not clipped

### Backend structure

- Domain helpers live under `app/domain/`; view/enrichment mapping under `app/application/`
- Extraction routes are split into jobs / records / rematches / verification, same `/extraction-jobs` prefix
- `MongoRepository` is a facade over persistence mixins; `result_fusion` stays a single module and is now v24

### Tests

- Frontend: 83 passed, 1 skipped (`pnpm test:run`)
- Backend: 190 passed (`pytest`)
- New coverage for PDF import progress, hardware auto-tune, Mongo schema, and machine verification

---

## quality-baseline-v3 — 2026-08-13

Main changes since `quality-baseline-v2` (2026-08-12).

### Color-page roles before extraction

- Page discovery v2 classifies pages by confidence (`color_plate`, `color_visual`, `mixed_visual`, `monochrome_visual`, `document`, `blank`)
- Full-document jobs build this page index first
- Color plates keep OCR and YOLO for linkage, but skip LLM semantic extraction and the body-text index

### Visual-only cards recover a body owner

- Sparse color-plate caption cards (e.g. `T03022:3`) recover unique rich body OCR and move `source_pages` off the plate
- Figure-item crops bind even when the label OCR is garbled (e.g. `3.102022:34` → `T03022:34`)
- Repair OCR punctuation around circled units (`T0302(②：34` → `T03022:34`)

### Paragraph fields stay on one artifact

- Stop wrapping OCR at the next different artifact ID
- Scope measurements, captions, and morphology to this ID’s span (drop the previous entry’s tail and later specimens)
- On rematch, replace already-fused fields that swallowed later IDs such as `T02037`
- Do not treat catalog prefixes such as `标本` as the vessel category

### Tests

- Fusion regressions for color-only cards, garbled figure labels, rematch field pollution, and catalog-prefix categories

---

## quality-baseline-v2 — 2026-08-12

Main changes and improvements since `quality-baseline-v1` (2026-07-28).

### Job elapsed time

- Persist `completed_at` when a job finishes; the UI prefers it when computing elapsed time
- Avoid rematch/apply refreshing `updated_at` and inflating elapsed time to hundreds of hours
- On startup, mark stale extraction jobs as failed and freeze their completion time

### Artifact cards and text evidence

- Paragraph enrichment upgrade (fusion v15→v16): backfill/upgrade category, texture, and morphological description from OCR paragraphs
- Upgrade short morphology values (e.g. “片状”) when OCR evidence is richer
- Keep measurement fields from swallowing the next artifact ID; attach units such as “厘米” when they wrap to the next line
- List/evidence APIs can persist paragraph enrichment (`paragraph_enrichment_version`)
- Catalog cards prefer morphology on the card and can fall back to `text_evidence`

### Preview layout (color plates are not column 1)

- Exclude color-plate pages when choosing the primary text page (`page_type=color_plate` or color-plate regions)
- The left preview column always prefers non-color body text; color plates remain optional third-column links
- Frontend `preview-document-page` helper as a safety net

### Color-plate captions ≠ catalog body text (e.g. M4:3 / 仲M4:3)

- **Do not treat color-plate OCR as text evidence**; plates are for association only
- Absorb/drop empty plate-caption cards (e.g. `4.玉锥形饰（仲M4：3）`) into the body-text artifact’s link metadata
- Normalize IDs by stripping tomb/unit prefixes (`仲M4:3` → `M4:3`) in fusion and entity linking
- Catalog UI hides caption-only empty plate cards so search does not show multiple empty hits

### Tests

- Added/extended coverage for plate-caption absorption, tomb-prefix entity merge, primary text page selection, and catalog empty-card filtering

---

## quality-baseline-v1 — 2026-07-28

First quality baseline: content preview, PDF navigation, artifact card linking, text-evidence extraction, color-plate/caption association, and verification UI. See each app’s `BASELINE.md`.
