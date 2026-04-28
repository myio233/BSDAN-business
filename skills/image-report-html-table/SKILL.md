---
name: image-report-html-table
description: Convert ASDAN/exschool market-report screenshots into HTML tables and Excel workbooks, verify round and city coverage, and prepare the structured files needed for downstream decision reconstruction. Use when the task is real report extraction rather than simulator-only testing.
---

# Image Report HTML Table

Use this skill when exschool market-report screenshots need to become reviewable HTML tables and Excel workbooks.

## What this covers

- ASDAN/exschool market-report long screenshots
- `image -> HTML table -> Excel` workflows
- validating round titles and city coverage before downstream reconstruction or modeling

## Current prerequisites and caveats

- `tesseract` must be installed and available on `PATH`
- the built-in exschool preset is currently resolved relative to the project checkout
- if the repo is checked out somewhere else, fix the hardcoded `ROOT` assumption first or provide a matching path alias

## Outputs

The script writes:

- one HTML table per image
- one combined Excel workbook per image, generated from the HTML table
- one structured Excel workbook per source workbook, with one sheet per city
- one validation workbook covering round detection and city coverage checks

## Default workflow

1. OCR each image with `tesseract`.
2. Detect round title from image text.
3. Detect `Market Report - <City>` section titles from image text.
4. If a verified source workbook exists, use it as the canonical table content after OCR validates the image title/sections.
5. Write one HTML table per image.
6. Read the HTML back into pandas and write the combined Excel workbook.
7. When a verified source workbook exists, also write a structured workbook with one city per sheet.
8. Write a validation workbook and review failures before using the outputs downstream.

## In this repo

For the current exschool task, use the built-in `exschool` preset. It assumes:

- images live under `exschool`
- verified source workbooks also live under `exschool`
- outputs go under `outputs/exschool_market_report_exports`

Run:

```bash
python3 skills/image-report-html-table/scripts/export_market_report_tables.py --preset exschool
```

If the goal is simulator-ready fixed-opponent data, follow the export with:

```bash
EXSCHOOL_MARKET_REPORT_DIR=outputs/exschool_market_report_exports/structured_xlsx \
python3 obos/reconstruct_exschool_decisions.py
```

## Generic mode

Use generic mode if the images are not the exschool set:

```bash
python3 skills/image-report-html-table/scripts/export_market_report_tables.py \
  --image-dir /abs/path/to/images \
  --output-dir /abs/path/to/output
```

Optional:
- `--source-xlsx-dir /abs/path/to/source/workbooks`
  Use when a verified workbook exists and should be treated as canonical after OCR validation.

## Validation expectations

Before claiming success, inspect:

- `validation_report.xlsx`
- whether every expected city appears
- whether each HTML file corresponds to the correct round
- whether the structured workbook sheet names match the OCR-detected city titles
- whether `outputs/exschool_inferred_decisions/all_round_reconstruction_summary.xlsx` and `ASSUMPTIONS.txt` look consistent if you also ran reconstruction

If OCR and source workbook disagree, stop and inspect the image manually instead of silently continuing.
