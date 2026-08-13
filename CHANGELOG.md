# Changelog

[English](./CHANGELOG.md) | [中文](./CHANGELOG中文.md)

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
