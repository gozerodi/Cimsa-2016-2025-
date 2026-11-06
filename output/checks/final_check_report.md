# Final check report

Date: 30 October 2026. Scope: `src/` 01–12, `notebooks/` 01–05, `output/`, `requirements.txt`. No new analysis was added; README.md was not changed.

---

## 1. Re-running the scripts (src/01 → 12)

**Method:** Before running, `output/` and `notebooks/` were backed up. The scripts were run in order with the project's virtual environment (`.venv/bin/python`, Python 3.12.9). Every CSV under `output/tables/` and `output/checks/` was then compared with the previous version: row and column counts, column names, cell values (relative tolerance 1e-9 for numbers, exact match for text).

**API:** The raw data cache (`output/tables/raw/`, 58 files) and `Data/External/CMO-Historical-Data-Monthly.xlsx` were used. No script called an API (no "fetched" line in the logs; raw file count 58 → 58).

| Script | Result | Time |
|---|---|---|
| 01_extract_cost_items | no errors | 33 s |
| 02_extract_esg_kpis | no errors | 159 s |
| 03_build_esg_kpis_annual | no errors | 1 s |
| 04_extract_financials_annual | no errors | 47 s |
| 05_fetch_evds | no errors (from cache) | 0 s |
| 06_build_master_table | no errors | 0 s |
| 07_extract_quarterly | no errors | 93 s |
| 08_reference_prices | no errors (from cache) | 1 s |
| 09_extract_fx_debt | no errors | 45 s |
| 10_extract_quarterly_tms29 | no errors | 47 s |
| 11_extract_sales_volumes | no errors | 151 s |
| 12_fetch_cement_ppi | no errors (from cache) | 0 s |

**Comparison result: 28 / 28 files identical. No differences.**

| File | Rows | Result |
|---|---|---|
| tables/annual_master.csv | 20 | identical |
| tables/annual_master_dictionary.csv | 97 | identical |
| tables/cement_ppi_monthly.csv | 182 | identical |
| tables/cost_items_annual.csv | 160 | identical |
| tables/cpi_monthly.csv | 133 | identical |
| tables/esg_kpis_annual.csv | 213 | identical |
| tables/esg_kpis_candidates.csv | 327 | identical |
| tables/financials_annual.csv | 676 | identical |
| tables/fx_debt_annual.csv | 832 | identical |
| tables/fx_rates_annual.csv | 240 | identical |
| tables/quarterly_cumulative.csv | 376 | identical |
| tables/quarterly_ratios_tms29.csv | 63 | identical |
| tables/quarterly_series.csv | 341 | identical |
| tables/reference_prices_annual.csv | 10 | identical |
| tables/sales_volumes_annual.csv | 72 | identical |
| tables/sales_volumes_candidates.csv | 106 | identical |
| checks/annual_master_checks.csv | 35 | identical |
| checks/cost_items_annual_checks.csv | 20 | identical |
| checks/esg_kpis_annual_checks.csv | 310 | identical |
| checks/esg_kpis_conflicts.csv | 73 | identical |
| checks/esg_report_boundaries.csv | 40 | identical |
| checks/financials_annual_checks.csv | 80 | identical |
| checks/fx_debt_checks.csv | 249 | identical |
| checks/fx_rates_checks.csv | 80 | identical |
| checks/quarterly_checks.csv | 291 | identical |
| checks/quarterly_tms29_checks.csv | 20 | identical |
| checks/reference_prices_checks.csv | 62 | identical |
| checks/sales_volumes_checks.csv | 67 | identical |

**Environment note:** In a first attempt with the system Python (`python3`), 10 scripts stopped at the import stage (without writing files) because `pdfplumber`, `dotenv` or `openpyxl` could not be found. 03 and 06 ran in that attempt. All scripts were then re-run from the start with `.venv`, and the comparison was made against the output of that run. **The scripts must be run with the `.venv` environment.**

---

## 2. Re-running the notebooks from scratch

With `.venv/bin/jupyter nbconvert --execute` (kernel: `.venv` Python 3.12.9; a run from a clean kernel, equivalent to "Restart & Run All").

| Notebook | Errors | Cells not run |
|---|---|---|
| 01_energy_analysis | 0 | 0 |
| 02_cost_structure_and_mix | 0 | 0 |
| 03_fx_exposure | 0 | 0 |
| 04_synthesis | 0 | 0 |
| 05_price_vs_cost | 0 | 0 |

The 26 charts under `output/figures/` were regenerated (file count 26 → 26).

---

## 3. Consistency

### 3a. Numbers in markdown vs table/output values

The numbers in each notebook's "What we see", interim findings and conclusion cells were compared with the same notebook's code outputs and with `annual_master.csv`. Main indicators checked: energy share, gross margin, segment margins, implied energy price, price-volume effects, market index gaps, log decomposition and shift-share terms, export sales share, FX loan shares and coverage ratios, net FX position, margin volatility, per-ton indicators, PPI changes.

Most numbers are consistent with the outputs. Corrected:

| # | Notebook / cell | Before | Correct value (source) | Correction |
|---|---|---|---|---|
| 1 | 02, section 4b "What we see" | 2016 → 2019 margin change −14.4 points | −14.46 → **−14.5** (shift-share table) | corrected |
| 2 | 02, section 4a "What we see" | Cement margin 12–26% in 2019–2022 | 2019 13.2%, 2020 25.8%, 2021 20.1%, 2022 18.2% → **13–26%** | corrected |
| 3 | 01, section 5 "What we see" | 2025 energy cost per ton of clinker "~70% above pre-crisis" | 26.9 / 16.2 (2016–2020 average) = **~66%** | corrected |
| 4 | 01, section 6 context | 2025 coal and PTF ~25% and ~45% above the 2016–2019 average | **~24%** and **~44%** (price table) | corrected |

### 3b. Contradictions between and within notebooks

| # | Contradiction | Correction |
|---|---|---|
| 1 | **Within 05:** Section 3(a) said "most of the jump in other cost is in 2023". The newly added 3b shows that the jump is not in 2023 but in the IAS 29 and SBS-inclusive restatement of 2022 (2022 nominal 23.5 → 2022r 46.2 → 2023 47.6 USD/ton). | The 3(a) text was aligned with 3b; "(c) 2022r → 2023: section 3b" was added to the list of periods in section 3. |
| 2 | **Within 04:** In notebook 04 the carbon liability appeared in four places as "user information, not verified". The source is `Cimsa_2025_03.pdf`, p. 37. The value was re-verified in the PDF: "Karbon yükümlülükleri 2.663.667 / 2.718.810 bin TL; CBS B.V.'nin karbon yükümlülükleri" (carbon liabilities TRY 2,663,667 / 2,718,810 thousand; carbon liabilities of CBS B.V.). | The section 4 note of 04, the risk table (after and evidence columns), the risk map commentary and the "hypotheses to verify with internal data" item were updated with the source. The value was not extracted into the tables; it remains context. |

**Consistency of the notebook 04 summaries with the final versions of 01, 02, 03 and 05: consistent.**
- **01 summary:** Energy share 60% → 21%. The decline comes mainly together with the price effect. The implied price tracks the market with a lag and smoothed. H2 partially. Same as 01.
- **02 summary:** ~half of the decline on the energy side, ~half in the denominator; ~16% may be related to the classification change. Gross margin +8.2 points, mainly within segments. Contribution of building materials ~−1.1 points. H2 partially, H3 margin part confirmed. Same as 02.
- **03 summary:** FX loan share 15% → 87%. FX loans / EBITDA proxy 2.7. Net FX position in Turkey balanced or net asset. H1 partially. Same as 03.
- **05 paragraph:** 2022r → 2023 (+7.6 = revenue −5.1, energy +14.2, other cost −1.4) and 2023 → 2025 (+0.4 = −2.1 / +5.5 / −3.0). Same as the output and section 5 of 05.
- **Different assessments of the same hypothesis:** None found. H1 partially (03, 04); H2 partially (01, 02, 04); H3 margin part confirmed (02, 04), volatility part insufficient evidence (04); H4 partially (04).
- **Different values for the same indicator:**
  - 2022 IAS 29 gross margin 11.4% (consolidated) and cement margin 11.5% (segment). Different indicators; not a contradiction.
  - The two versions of 2022 (nominal / IAS 29) are stated separately everywhere.

### 3c. Implementation of the methodology decisions

Verified as implemented:
- **Views and units:** `view` and `basis` columns; units converted to TRY.
- **2016 total check:** The TRY 7.25 million difference in the 2016 total check is reported.
- **Electricity share:** Only electricity supply lines are used for the electricity share.
- **FX conversion:** FX choice in 06: EVDS annual average in nominal years, year-end rate of the report year in IAS 29 years.
- **Preferred quarterly value:** `value_preferred`, with a TRY 1,000 tolerance.
- **Year-end rate adjustment in IAS 29 years:** Deliberately not applied; stated in the notebooks.
- **Rolling 2022 forward:** Rolling 2022 forward to December 2025 with the CPI and the 13.5% classification assumption.
- **FX debt and shocks:** Loans excluding leases, the 2018 and 2023 exceptions, the 1.4438 factor, the 20% shock threshold.
- **IAS 29 quarterly method:** IAS 29 quarterly method; volatility measured with gross margin.
- **Risk map and interest cover:** 2×2 matrix removed; note that interest cover is approximate is present.
- **Per-ton analysis:** PPI series and per-ton definitions.
- **Physical and volume data:** 2016–2019 MWh not used; 2023 clinker total; 2024 alternative fuel drop; 2019 volumes excluded; sector data used only as context.
- **IAS 29 line in charts:** Charts have the IAS 29 line.

Not fully implemented or left open between the decisions and the notebooks (not corrected; decision left to the user):

1. **2024 Q4 energy ratios "flagged":** In `quarterly_ratios_tms29.csv` the 2024 Q4 `energy_share_production_cost` and `energy_to_revenue` rows have `use = analysis` and no warning in the note column. No notebook uses these ratios (04 uses only the gross margin), so there is no effect on the results. Not corrected because it would change the output of script 10.
2. **"Main chart 2021 = 100":** The main market comparison chart of notebook 01 (`01_cimsa_vs_market_energy_index.png`) uses 2020 = 100. 2021 = 100 appears only in the sensitivity chart (`01_base_year_sensitivity.png`). The notebook 01 text states this "will be done in the presentation"; to be handled at the presentation stage.

---

## 4. requirements.txt

Third-party packages imported directly in the scripts and notebooks:
- pandas, numpy, pdfplumber, matplotlib, requests, python-dotenv (`dotenv`), eptr2;
- openpyxl (via `pd.read_excel`);
- jupyter / ipykernel (to run the notebooks).

- **Missing:** `numpy` (imported directly in src/03, 06, 07 and all notebooks) → **added.**
- The others were already listed. Versions in `.venv`: pandas 3.1.0, numpy 2.5.4, pdfplumber 0.11.10, matplotlib 3.11.2, openpyxl 3.1.5, eptr2 1.3.9, requests 2.34.2, python-dotenv 1.2.4, jupyter 1.1.1, ipykernel 7.4.0. Versions are not pinned in the file.

---

## 5. Files changed

- `notebooks/01_energy_analysis.ipynb`: 2 numbers (3a #3, #4).
- `notebooks/02_cost_structure_and_mix.ipynb`: 2 numbers (3a #1, #2).
- `notebooks/04_synthesis.ipynb`: carbon source (3b #2).
- `notebooks/05_price_vs_cost.ipynb`: section 3(a) text and list of periods (3b #1).
- `requirements.txt`: `numpy` added.
- `output/checks/final_check_report.md`: this report (new).
- Re-running the scripts and notebooks rewrote `output/tables`, `output/checks` and `output/figures`. Tables and check files are identical.

## 6. Open items (summary)

1. Adding a warning to the 2024 Q4 energy ratios in the table (no effect on results).
2. Switching the main market chart to 2021 = 100 (presentation stage).
3. The scripts only run with `.venv`; recommended to state this in the README.
