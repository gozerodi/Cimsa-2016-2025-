"""
05 – Fetches FX rate and CPI data from CBRT EVDS (TCMB EVDS).

API: https://evds3.tcmb.gov.tr/igmevdsms-dis/ ; the key is sent in the HTTP header ("key").
     The key is read from EVDS_API_KEY in the .env file and is never written in the code.
Series:
  TP.DK.USD.A.YTL  USD/TRY FX buying rate (daily)
  TP.DK.EUR.A.YTL  EUR/TRY FX buying rate (daily)
  TP.FG.J0         CPI general index (2003=100, monthly)
Raw responses are saved year by year under output/tables/raw/; if a file exists the API is not called again
(to re-fetch: python src/05_fetch_evds.py --refresh).

Outputs:
  output/tables/fx_rates_annual.csv   (annual and quarterly average + period-end rate)
  output/tables/cpi_monthly.csv       (monthly CPI)
  output/checks/fx_rates_checks.csv   (EVDS rates vs the report rates in financials_annual.csv)
"""
from datetime import date
import json
import os
from pathlib import Path
import sys

import pandas as pd
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "output" / "tables" / "raw"
FX_OUT = ROOT / "output" / "tables" / "fx_rates_annual.csv"
CPI_OUT = ROOT / "output" / "tables" / "cpi_monthly.csv"
CHECK_OUT = ROOT / "output" / "checks" / "fx_rates_checks.csv"
FIN_IN = ROOT / "output" / "tables" / "financials_annual.csv"

BASE_URL = "https://evds3.tcmb.gov.tr/igmevdsms-dis/"
START_YEAR = 2015
MAX_ROWS = 1000  # maximum number of records the API returns per request
SERIES = {
    "usd": "TP.DK.USD.A.YTL",
    "eur": "TP.DK.EUR.A.YTL",
    "cpi": "TP.FG.J0",
}


def _get(code: str, start: str, end: str, api_key: str) -> dict:
    url = f"{BASE_URL}series={code}&startDate={start}&endDate={end}&type=json"
    resp = requests.get(url, headers={"key": api_key}, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    if data.get("totalCount", 0) >= MAX_ROWS:
        sys.exit(f"{code} {start}-{end}: {MAX_ROWS}-record limit reached; narrow the date range.")
    return {"meta": {"series": code, "url_without_key": url, "fetched_on": date.today().isoformat()},
            "response": data}


def fetch(code: str, refresh: bool = False):
    """Fetches the series year by year (the API returns at most 1000 records per request) and saves the raw JSON.
    If a saved file exists the API is not called. The current year's file holds data up to the fetch date."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    api_key = None
    paths = []
    for y in range(START_YEAR, date.today().year + 1):
        out = RAW_DIR / f"evds_{code}_{y}.json"
        if not out.exists() or refresh:
            if api_key is None:
                load_dotenv(ROOT / ".env")
                api_key = os.environ.get("EVDS_API_KEY")
                if not api_key:
                    sys.exit("EVDS_API_KEY not found (check the .env file).")
            end = min(date(y, 12, 31), date.today()).strftime("%d-%m-%Y")
            raw = _get(code, f"01-01-{y}", end, api_key)
            out.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"  fetched: {code} {y} ({raw['response'].get('totalCount')} records)")
        paths.append(out)
    return paths


def load_series(paths, code: str) -> pd.DataFrame:
    col = code.replace(".", "_")
    items = []
    for p in paths:
        items += json.loads(p.read_text(encoding="utf-8"))["response"]["items"]
    df = pd.DataFrame({"date_str": [it["Tarih"] for it in items],
                       "value": [it.get(col) for it in items]})
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.drop_duplicates("date_str").reset_index(drop=True)


def build_fx(paths) -> pd.DataFrame:
    rows = []
    for cur in ("usd", "eur"):
        code = SERIES[cur]
        df = load_series(paths[cur], code)
        df["date"] = pd.to_datetime(df["date_str"], format="%d-%m-%Y")
        df = df.dropna(subset=["value"])  # weekends / holidays are empty
        df["year"] = df["date"].dt.year
        df["quarter"] = df["date"].dt.quarter
        src = f"output/tables/raw/evds_{code}_YYYY.json"
        groups = [("FY", g, y) for y, g in df.groupby("year")] + \
                 [(f"Q{q}", g, y) for (y, q), g in df.groupby(["year", "quarter"])]
        for period, g, y in groups:
            g = g.sort_values("date")
            last = g.iloc[-1]
            complete = (period == "FY" and last["date"].month == 12 and last["date"].day >= 24) or \
                       (period != "FY" and last["date"].month % 3 == 0 and last["date"].day >= 24)
            note_common = f"{len(g)} business days; first {g.iloc[0]['date_str']}, last {last['date_str']}"
            if not complete:
                note_common += "; PERIOD NOT YET COMPLETE (partial data)"
            for kind, val, extra in [
                ("average", g["value"].mean(), "simple average of daily buying rates in the period"),
                ("period_end", last["value"], f"value on the last business day of the period ({last['date_str']})"),
            ]:
                rows.append({"year": int(y), "period": period, "item": f"fx_{cur}_try_{kind}",
                             "value": round(float(val), 6), "unit": f"TRY/{cur.upper()}", "basis": "nominal",
                             "restated": False, "source_file": src, "source_page": None,
                             "note": f"EVDS {code} (FX buying); {extra}; {note_common}"})
    out = pd.DataFrame(rows)
    order = {"FY": 0, "Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}
    out["_o"] = out["period"].map(order)
    return out.sort_values(["item", "year", "_o"]).drop(columns="_o").reset_index(drop=True)


def build_cpi(paths) -> pd.DataFrame:
    df = load_series(paths, SERIES["cpi"]).dropna(subset=["value"])
    # EVDS monthly date format "YYYY-M"
    ym = df["date_str"].str.split("-", expand=True).astype(int)
    df["year"], df["month"] = ym[0], ym[1]
    src = f"output/tables/raw/evds_{SERIES['cpi']}_YYYY.json"
    return pd.DataFrame({
        "year": df["year"], "period": df["month"].map(lambda m: f"M{m:02d}"), "item": "cpi_index",
        "value": df["value"], "unit": "index_2003=100", "basis": "nominal", "restated": False,
        "source_file": src, "source_page": None, "note": f"EVDS {SERIES['cpi']} CPI general index",
    }).sort_values(["year", "period"]).reset_index(drop=True)


def build_checks(fx: pd.DataFrame, paths) -> pd.DataFrame:
    fin = pd.read_csv(FIN_IN)
    fin = fin[fin["item"].str.startswith("fx_rate_")].copy()
    fin[["cur", "kind"]] = fin["item"].str.extract(r"fx_rate_(usd|eur)_(average|period_end)")
    evds = fx[fx["period"] == "FY"].copy()
    evds[["cur", "kind"]] = evds["item"].str.extract(r"fx_(usd|eur)_try_(average|period_end)")

    # Extra comparison for period end: value on the first business day of the following year
    first_next = {}
    for cur in ("usd", "eur"):
        df = load_series(paths[cur], SERIES[cur]).dropna(subset=["value"])
        df["date"] = pd.to_datetime(df["date_str"], format="%d-%m-%Y")
        for y, g in df.groupby(df["date"].dt.year):
            first_next[(cur, y - 1)] = (g.sort_values("date").iloc[0]["value"], g.sort_values("date").iloc[0]["date_str"])

    rows = []
    for _, r in fin.iterrows():
        e = evds[(evds["year"] == r["year"]) & (evds["cur"] == r["cur"]) & (evds["kind"] == r["kind"])]
        ev = e["value"].iloc[0] if len(e) else None
        rep = r["value"]
        diff = (rep / ev - 1) * 100 if pd.notna(rep) and ev else None
        row = {"year": r["year"], "currency": r["cur"].upper(), "rate_type": r["kind"],
               "report_value": rep, "report_source_file": r["source_file"], "restated": r["restated"],
               "evds_value": ev, "diff_pct": round(diff, 3) if diff is not None else None,
               "evds_next_first_business_day": None, "diff_pct_vs_next_first_day": None, "note": ""}
        if r["kind"] == "period_end" and (r["cur"], r["year"]) in first_next:
            nv, nd = first_next[(r["cur"], r["year"])]
            row["evds_next_first_business_day"] = nv
            row["diff_pct_vs_next_first_day"] = round((rep / nv - 1) * 100, 3) if pd.notna(rep) else None
            row["note"] = (f"EVDS year-end = last business day of the year; also compared with the value dated {nd} "
                           "(in EVDS the rate for a date is the rate valid that day, announced on the previous business day)")
        rows.append(row)
    return pd.DataFrame(rows).sort_values(["currency", "rate_type", "year", "restated"]).reset_index(drop=True)


def main():
    refresh = "--refresh" in sys.argv
    paths = {k: fetch(code, refresh) for k, code in SERIES.items()}
    fx = build_fx(paths)
    FX_OUT.parent.mkdir(parents=True, exist_ok=True)
    fx.to_csv(FX_OUT, index=False, encoding="utf-8")
    cpi = build_cpi(paths["cpi"])
    cpi.to_csv(CPI_OUT, index=False, encoding="utf-8")
    checks = build_checks(fx, paths)
    CHECK_OUT.parent.mkdir(parents=True, exist_ok=True)
    checks.to_csv(CHECK_OUT, index=False, encoding="utf-8")
    print(f"FX   : {FX_OUT.relative_to(ROOT)} ({len(fx)} rows)")
    print(f"CPI  : {CPI_OUT.relative_to(ROOT)} ({len(cpi)} months, {cpi['year'].min()}-{cpi['year'].max()})")
    print(f"Check: {CHECK_OUT.relative_to(ROOT)} ({len(checks)} rows)")


if __name__ == "__main__":
    main()
