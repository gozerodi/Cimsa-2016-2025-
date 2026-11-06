# Çimsa's Transformation, 2016–2025

A review of profitability and resilience to external shocks using public data.

**[Full report (PDF)](reports/Cimsa_Transformation_2016-2025.pdf)**

## Overview

Between 2016 and 2025, Çimsa went through what it describes as a three-way transformation: from cement to building materials, from local to global, and from grey to green.

Over the same decade the company changed its corporate structure five times. In 2017 the US white cement business, Cimsa Americas, joined the group. In 2021 the international white cement operations were transferred to SBS. In 2022 the plants in Niğde, Kayseri and Ankara and the Central Anatolian ready-mix operations were sold. In 2023 Çimsa took control of SBS with a 50.1% stake, and in 2024 the Irish building materials producer Mannok was consolidated.

The review asks whether this transformation made the company more profitable and more resilient to energy and currency shocks. It covers five areas: profitability, exposure to energy costs, the source of the margin recovery after 2022, currency risk and the debt structure, and a synthesis of the findings.

## Key findings

**The company grew, but its margin did not return to 2016 levels.** The EBITDA proxy in dollar terms rose from USD 121 million in 2016 to USD 212 million in 2025, while gross margin fell from 32% to 20%.

**Energy's share of costs fell, but no reduction in energy risk was observed.** Energy's share of production cost dropped from a peak of 60% in 2022 to 21% in 2025. About half of that decline came with growth in other cost items. Physical energy consumption did not change materially, and the company's energy cost followed market prices with a lag.

**The margin recovery after 2022 came with the cement margin, not with the new portfolio.** Almost all of the 8.2-point rise in gross margin between 2022 and 2025 came with a better cement margin; the portfolio change contributed −1.2 points. The cement improvement came mostly with lower energy cost per tonne, while revenue per tonne declined.

**Margins fell in currency shocks despite a high share of foreign revenue.** In the 2018 and 2021–2022 shocks, gross margin fell 9–16 points year on year in the shock quarter or the one that followed. The 2021–2022 shock coincided with the energy crisis, so the two effects cannot be separated.

**Currency exposure appears to have shifted to the overseas subsidiaries.** The net foreign-currency position of the Turkish companies turned from −USD 31 million to +USD 20 million. At group level, the foreign-currency share of bank loans rose from 15% to 87%, and foreign-currency loans grew from 0.3 to 2.7 times the EBITDA proxy.

Taken together, the data do not show that the transformation reduced risk; it appears to have moved, from a currency gap on the Turkish balance sheet to foreign-currency debt carried by the overseas subsidiaries.

## Methodology and data

### Data sources

- Çimsa's 40 quarterly and annual financial reports for 2016–2025 (Public Disclosure Platform, KAP) and its 10 integrated annual reports, parsed from PDF with pdfplumber. The financial reports are read by `src/01`, `04`, `07`, `09` and `10`; the integrated reports by `src/02` and `11`.
- CBRT EVDS API: USD/TRY and EUR/TRY buying rates and CPI (`src/05`), and the domestic producer price index for cement, lime and plaster (`src/12`).
- EPİAŞ Transparency Platform API, accessed through the eptr2 package: hourly day-ahead electricity price, PTF (`src/08`).
- World Bank Commodity Price Data ("Pink Sheet"): monthly Australian and South African coal prices, read from the downloaded file in `Data/External/` (`src/08`).

### Key rules and conversions

- Each year's figures are taken from that year's own report. Restated figures from later reports are kept separately and used only for comparisons within the same report.
- From the 2023 annual report onward, the financial statements are prepared under IAS 29. Figures for 2016–2022 (nominal) and 2023–2025 (IAS 29) are therefore treated as separate series: levels are compared only within a series, while ratios and dollar equivalents are used across series.
- Dollar amounts use CBRT EVDS buying rates: the annual average in nominal years and the year-end rate in IAS 29 years. The rates quoted in the reports are not used, because they are not consistent across years.
- Quarterly figures are derived from the cumulative interim reports; for the second and third quarters, the reports' own "last three months" columns are used. A currency shock quarter is one in which the average USD/TRY rate rose at least 20% on the previous quarter.
- The energy market index combines the coal price and the PTF, weighted each year by electricity's share of energy consumption. The report uses 2021 = 100 as the base year, because coal prices were unusually low in the 2020 pandemic year.
- EBITDA proxy = operating profit + depreciation and amortisation. This is not the EBITDA figure reported by the company; it is used because it can be calculated on the same definition for all ten years.

### Quality checks

Each year, the cost items extracted from the cost note are reconciled to total production cost. Values are also compared across reports: a year's own figures against its restated figures, annual totals against the sum of quarters, and EVDS exchange rates against the rates quoted in the reports. The results are written to `output/checks/`. When the scripts were re-run from scratch, all 28 tables and check files were identical to the previous run and the five notebooks ran without errors (`output/checks/final_check_report.md`).

### Limitations

This is an outside-in review based only on public data. It does not compare Çimsa with other cement producers or assess the price paid for Mannok, so counterfactual questions cannot be answered. The 2013–2015 financial reports are scanned images and could not be read, which sets the start of the period at 2016. As no free petcoke price series is available, coal prices serve as an approximate benchmark for the company's main fuel, and the electricity market price is not the company's actual purchase price. Energy costs are not reported by segment, so energy cost per tonne uses the group energy cost, and the international white cement operations (SBS) cannot be separated from the cement segment. Physical energy data are limited to 2020–2025, and the nominal quarterly series ends in the third quarter of 2023.

## Repository structure

```
Data/                    Input files; not modified by the code
  FinancialStatements/   Quarterly and annual financial reports, 2016–2025 (40 PDFs)
  IntegratedReports/     Integrated annual reports, 2016–2025 (10 PDFs)
  External/              World Bank Pink Sheet monthly prices (Excel)
  Presentation/          Company presentation on Mannok (reference only; not read by the code)
src/                     Extraction and table-building scripts, run in numbered order
notebooks/               Analysis notebooks
output/
  tables/                Clean tables used in the analysis; raw/ holds cached API responses
  checks/                Data quality checks and the final check report
  figures/               Charts produced by the notebooks
reports/                 Final report (PDF)
```

Scripts in `src/`:

- `01_extract_cost_items.py` – cost items from the annual financial reports
- `02_extract_esg_kpis.py` – candidate physical energy and production indicators from the integrated reports
- `03_build_esg_kpis_annual.py` – clean annual table of physical indicators
- `04_extract_financials_annual.py` – revenue breakdown, segment data, profit and other financial items
- `05_fetch_evds.py` – exchange rates and CPI from CBRT EVDS
- `06_build_master_table.py` – annual master table with dollar equivalents
- `07_extract_quarterly.py` – nominal quarterly series, Q1 2016 to Q3 2023
- `08_reference_prices.py` – coal and electricity (PTF) reference prices
- `09_extract_fx_debt.py` – loans by currency, foreign-currency position and sensitivity
- `10_extract_quarterly_tms29.py` – quarterly ratios for the IAS 29 period, Q4 2023 to Q4 2025
- `11_extract_sales_volumes.py` – sales volumes and sector data from the integrated reports
- `12_fetch_cement_ppi.py` – cement producer price index from CBRT EVDS

Notebooks in `notebooks/`:

- `01_energy_analysis.ipynb` – energy share, implied energy price, market index and physical intensity
- `02_cost_structure_and_mix.ipynb` – decomposition of the energy share, segment margins and mix effect
- `03_fx_exposure.ipynb` – foreign revenue, currency shocks and foreign-currency debt
- `04_synthesis.ipynb` – profitability, margin volatility, debt capacity and risk map
- `05_price_vs_cost.ipynb` – price and unit cost per tonne in the cement segment

## Reproducing the analysis

The analysis requires Python 3.12. Create a virtual environment, install the dependencies and set up the credentials file:

```
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Enter `EVDS_API_KEY`, `EPTR_USERNAME` and `EPTR_PASSWORD` in `.env`. Then run the scripts in order, followed by the notebooks:

```
for f in src/*.py; do python "$f"; done
jupyter nbconvert --to notebook --execute --inplace notebooks/*.ipynb
```

Raw EVDS and EPİAŞ responses are cached year by year under `output/tables/raw/`. When a cached file exists, `src/05`, `08` and `12` do not call the API and do not read the credentials; to fetch the data again, run the script with `--refresh`.

## Disclaimer

This is an independent review based on public data. It has no affiliation with Çimsa Çimento Sanayi ve Ticaret A.Ş. and does not constitute investment advice. The company reports in `Data/` are the company's public publications and are included only so that the analysis can be reproduced.
