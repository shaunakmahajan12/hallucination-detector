# Tableau Public dashboard

An interactive evaluation dashboard for the detector, generated as a Tableau workbook from the CSVs in `data/`.
Regenerate them any time with `python3 scripts/export_tableau.py`. The script checks every CSV against `results/`
before writing (F1, AUROC, ECE, balanced accuracy), so the dashboard only shows numbers the repo already reports.

## The data

| File | Rows | Grain | Used for |
|---|--:|---|---|
| `ragtruth_predictions.csv` | 38,917 | one test sentence per method | threshold slider, breakdowns by task and generator LLM |
| `headline.csv` | 5 | one method per scope | KPI tiles (AUROC can't be computed inside Tableau) |
| `threshold_curve.csv` | 101 | one threshold (0.00 to 1.00) | precision / recall / F1 curve |
| `aggrefact_predictions.csv` | 29,320 | one LLM-AggreFact claim | per-dataset drill-down |
| `aggrefact_summary.csv` | 11 | one dataset | balanced accuracy and AUROC per dataset |
| `calibration_bins.csv` | 20 | one score bin per version | reliability diagram |
| `error_rates.csv` | 33 | one slice | where the model misses |
| `speed.csv` | 4 | one method and setting | time per sentence |

Conventions: `label` 1 means hallucinated (unsupported). A higher `score` means more likely hallucinated.
The Gemini judge answers 0 or 1, so its `score` is binary and its AUROC is only a rough figure.
The Gemini sample is 987 sentences with 70 hallucinated: say "comparable", never "beats", for differences this small.

## Build the workbook

The workbook is generated, not hand-built. `build_workbook.py` writes the Threshold parameter, the calculated fields,
ten worksheets and the dashboard into a base workbook that only has the CSV connections:

1. `python3 scripts/export_tableau.py` to refresh `data/`.
2. In Tableau, connect each `data/*.csv` as its own data source (Data > New Data Source > Text file) and save that
   workbook. This is the base. Tableau writes the connection blocks and their internal names.
3. Close it in Tableau, then `python3 tableau/build_workbook.py <base.twb> <out.twb>` and open `<out.twb>`.

The builder repairs a source whose table points at the wrong CSV, and refreshes a source whose CSV gained columns.

Layout (1200 x 1882, fixed): a navy header, then five numbered sections. 1. How good is it (full test results and
the Gemini sample), 2. Pick a threshold (slider over Live Metrics, F1 by task, F1 by answer-writing LLM, with a
click-to-filter action from the task chart), 3. How the scores behave (threshold curve, calibration), 4. Does it
generalize and where does it fail (LLM-AggreFact, share caught by hallucination type), 5. What it costs (speed).
The slider starts at the validation threshold 0.650467, so Live Metrics matches the results table.

Tableau 2026.3 quirks found while building (each checked against Tableau's own load log):
- A sheet that lists `<datasource name='Parameters' />` in its view drops every field that reads the parameter.
  The parameter's dependency block alone is enough.
- Calculated fields must have their raw input columns (`score`, `label`) declared in the sheet's dependencies.
- `zone-style` must come after a zone's child zones.
- A dimension used only as a tooltip on an aggregated or disaggregated view is rejected as an invalid field.

## Publish

Publishing needs the free **Tableau Public** app (Desktop Public Edition); regular Tableau Desktop asks for a
license. Tableau Public also refuses live CSV connections, so:

1. Export a packaged workbook (File > Export Packaged Workbook).
2. `python tableau/package_extracts.py <in.twbx> <out.twbx>` writes a Hyper file per CSV and points each data
   source at it, the way Tableau's sample workbooks connect to Hyper. It needs `pip install tableauhyperapi` on
   Python 3.11 or 3.12. An `<extract>` block added next to the CSV connection fails to load in 2026.3.
3. Open `<out.twbx>` in Tableau Public, then File > Save to Tableau Public As. Add the link to the repo README.
