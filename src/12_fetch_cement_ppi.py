"""
12 – Cement producer price index (TÜİK domestic PPI sub-item), TCMB EVDS.

Series: TP.TUFE1YI.T69 "3.14.5. Çimento, kireç ve alçı" (cement, lime and plaster; data group bie_tufe1yi, monthly; NACE 23.5).
        NACE 23.51 (cement only) was not found as a separate series in EVDS; 23.5 is used (also includes lime and plaster).
Method: uses fetch() and load_series() from 05_fetch_evds.py (same raw file format; key: EVDS_API_KEY in .env).
        Raw responses are saved year by year under output/tables/raw/; if a file exists the API is not called again (re-fetch with --refresh).
Output: output/tables/cement_ppi_monthly.csv (long format)
  item = cement_ppi_index          (monthly, period = M01..M12)
  item = cement_ppi_annual_avg     (annual average, period = FY; only years with all 12 months)
  item = cement_ppi_nominal_yoy_pct, cpi_annual_avg_yoy_pct, cement_ppi_real_yoy_pct
         (real change = (1 + PPI annual avg. change) / (1 + CPI annual avg. change) − 1; CPI: cpi_monthly.csv)
"""
import importlib.util
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "tables" / "cement_ppi_monthly.csv"
CPI_IN = ROOT / "output" / "tables" / "cpi_monthly.csv"
SERIES = "TP.TUFE1YI.T69"
SERIES_NAME = "Domestic PPI 3.14.5 Cement, lime and plaster (NACE 23.5)"

_spec = importlib.util.spec_from_file_location("fetch_evds", ROOT / "src" / "05_fetch_evds.py")
evds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(evds)


def main():
    paths = evds.fetch(SERIES, refresh="--refresh" in sys.argv)
    df = evds.load_series(paths, SERIES).dropna(subset=["value"])
    ym = df["date_str"].str.split("-", expand=True).astype(int)  # monthly date format "YYYY-M"
    df["year"], df["month"] = ym[0], ym[1]
    src = f"output/tables/raw/evds_{SERIES}_YYYY.json"
    base = {"unit": "index", "basis": "nominal", "restated": False, "source_file": src, "source_page": None}
    monthly = pd.DataFrame({
        "year": df["year"], "period": df["month"].map(lambda m: f"M{m:02d}"), "item": "cement_ppi_index",
        "value": df["value"], **base, "note": f"EVDS {SERIES} {SERIES_NAME}",
    })
    # annual average (full years only)
    cnt = df.groupby("year")["value"].agg(["mean", "size"])
    full = cnt[cnt["size"] == 12]
    cpi = pd.read_csv(CPI_IN)
    cpi_avg = cpi.groupby("year")["value"].agg(["mean", "size"])
    cpi_avg = cpi_avg[cpi_avg["size"] == 12]["mean"]
    rows = []
    for y, r in full.iterrows():
        rows.append({"year": y, "period": "FY", "item": "cement_ppi_annual_avg", "value": r["mean"], **base,
                     "note": f"simple average of the 12 monthly index values; {SERIES}"})
        if y - 1 in full.index:
            nom = r["mean"] / full.loc[y - 1, "mean"] - 1
            rows.append({"year": y, "period": "FY", "item": "cement_ppi_nominal_yoy_pct", "value": nom * 100,
                         **{**base, "unit": "%"}, "note": "change in annual average PPI (cement, lime, plaster)"})
            if y in cpi_avg.index and y - 1 in cpi_avg.index:
                c = cpi_avg[y] / cpi_avg[y - 1] - 1
                rows.append({"year": y, "period": "FY", "item": "cpi_annual_avg_yoy_pct", "value": c * 100,
                             **{**base, "unit": "%", "source_file": "output/tables/cpi_monthly.csv"},
                             "note": "change in annual average CPI (2003=100)"})
                rows.append({"year": y, "period": "FY", "item": "cement_ppi_real_yoy_pct",
                             "value": ((1 + nom) / (1 + c) - 1) * 100, **{**base, "unit": "%"},
                             "note": "real change = (1 + PPI change) / (1 + CPI change) − 1"})
    out = pd.concat([monthly, pd.DataFrame(rows)], ignore_index=True).sort_values(["item", "year", "period"])
    out.to_csv(OUT, index=False, encoding="utf-8")
    fy = out[out["period"] == "FY"].pivot_table(index="year", columns="item", values="value")
    print(fy.loc[2015:2025].round(2).to_string())
    print(f"Table: {OUT.relative_to(ROOT)} ({len(out)} rows; monthly {len(monthly)}: "
          f"{df['year'].min()}-{df['month'].iloc[0]:02d} – {df['year'].max()}-{df['month'].iloc[-1]:02d})")


if __name__ == "__main__":
    main()
