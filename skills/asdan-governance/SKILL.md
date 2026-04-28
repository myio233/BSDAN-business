---
name: asdan-governance
description: Coordinate long ASDAN tasks by separating source data, derived outputs, simulator behavior, and documentation, then choose the smallest validation set that actually covers the touched layer. Use when a task spans multiple turns or mixes data refresh with app/model work.
---

# ASDAN Governance

Use this skill before starting a long task in this repo.

## Working split

- Treat `exschool/`, `WYEF/`, `training1/`, and `southtraining/` as source data.
- Treat `outputs/` and `generated_reports/` as derived artifacts that should be reproducible from commands.
- Treat `exschool_game/` as simulator runtime behavior.
- Treat `obos/` and `结果/` as the data-processing and model-fitting workbench.

## Default workflow

1. Classify the task first: `docs only`, `data refresh`, `simulator behavior`, or `mixed`.
2. Keep data refresh separate from behavior changes when possible.
3. Before overwriting a derived artifact, record the exact command that produced it.
4. If a script depends on machine-local assumptions, document the assumption instead of hiding it.

## Validation matrix

- `docs only`
  Verify paths, commands, artifact names, and caveats against the repo.
- `data refresh`
  Review workbook or JSON validation artifacts, not just command exit status.
- `simulator behavior`
  Run targeted pytest for the touched rules and report any untested surfaces.
- `mixed`
  Validate each layer separately and report the gap between them.

## Current repo caveats

- `requirements-exschool-game.txt` covers the simulator Python dependencies, not every research or OCR dependency.
- Real report extraction also needs `tesseract`.
- The current exschool extraction scripts pin `ROOT` to `.`; on another checkout, normalize that path assumption before running them.
- The pytest suite covers simulator behavior, not the OCR/export bootstrap scripts.
