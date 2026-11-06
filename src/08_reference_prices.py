"""
08 – Market reference energy prices: World Bank coal prices and EPİAŞ day-ahead market price (PTF, electricity).

Inputs:
  Data/External/CMO-Historical-Data-Monthly.xlsx ("Monthly Prices")  -> Coal, Australian / Coal, South African ($/mt)
  EPİAŞ Transparency Platform PTF (hourly, TRY/MWh), via the eptr2 package -> raw CSV: output/tables/raw/epias_ptf_YYYY.csv
      Credentials are read from EPTR_USERNAME / EPTR_PASSWORD in the .env file and are never written in the code.
      If the raw file exists the API is not called again (to re-fetch: python src/08_reference_prices.py --refresh).
  output/tables/raw/evds_TP.DK.USD.A.YTL_YYYY.json (05_fetch_evds.py) -> monthly average USD/TRY
  output/tables/annual_master.csv (view = own)                        -> source of the electricity weight (for checks)
Outputs:
  output/tables/reference_prices_annual.csv   (annual; wide format)
  output/checks/reference_prices_checks.csv   (PTF missing hours/days, FX matching, weight source, series notes)

PTF dollar conversion: monthly average PTF (TRY/MWh) / average of the same month's EVDS daily buying rates;
annual USD/MWh = simple average of the 12 monthly USD values.
"""
from datetime import date, timedelta
import importlib.util
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "output" / "tables" / "raw"
CMO_IN = ROOT / "Data" / "External" / "CMO-Historical-Data-Monthly.xlsx"
MASTER_IN = ROOT / "output" / "tables" / "annual_master.csv"
OUT = ROOT / "output" / "tables" / "reference_prices_annual.csv"
CHECK_OUT = ROOT / "output" / "checks" / "reference_prices_checks.csv"

YEARS = range(2016, 2026)
COAL_COLS = {"Coal, Australian": "coal_australia_usd_t", "Coal, South African **": "coal_south_africa_usd_t"}
EVDS_USD = "TP.DK.USD.A.YTL"


def _load_evds_module():
    spec = importlib.util.spec_from_file_location("fetch_evds", ROOT / "src" / "05_fetch_evds.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- coal
def load_coal() -> pd.DataFrame:
    raw = pd.read_excel(CMO_IN, sheet_name="Monthly Prices", header=None)
    header = raw.iloc[4].tolist()  # series names in row 5, units in row 6
    cols = {header.index(name): out for name, out in COAL_COLS.items()}
    units = {out: raw.iloc[5, i] for i, out in cols.items()}
    assert all(u == "($/mt)" for u in units.values()), units
    df = raw.iloc[6:, [0] + list(cols)].copy()
    df.columns = ["ym"] + list(cols.values())
    df = df[df["ym"].astype(str).str.match(r"^\d{4}M\d{2}$")]
    df["year"] = df["ym"].str[:4].astype(int)
    df["month"] = df["ym"].str[5:].astype(int)
    for c in cols.values():
        df[c] = pd.to_numeric(df[c], errors="coerce")  # "…" = no data
    return df[df["year"].isin(YEARS)].reset_index(drop=True)


# ---------------------------------------------------------------- PTF
def fetch_ptf(refresh: bool = False):
    """Fetches the PTF year by year and saves it as raw CSV. If the file exists the API is not called.
    To guard against time-zone shifts (Turkey was UTC+2 in 2016) a one-day buffer is kept at the year boundaries;
    filtering by local date is done in load_ptf."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    client = None
    paths = []
    for y in YEARS:
        out = RAW_DIR / f"epias_ptf_{y}.csv"
        if not out.exists() or refresh:
            if client is None:
                from eptr2 import EPTR2
                # recycle_tgt=False: the session ticket is not written to disk
                client = EPTR2(dotenv_path=str(ROOT / ".env"), recycle_tgt=False)
            # the API accepts at most ~1 year per request; each year is fetched in two parts
            chunks = [(date(y, 1, 1) - timedelta(days=1), date(y, 6, 30)),
                      (date(y, 7, 1), min(date(y + 1, 1, 1), date.today()))]
            df = pd.concat([client.call("mcp", start_date=s.isoformat(), end_date=e.isoformat())
                            for s, e in chunks], ignore_index=True)
            df["fetched_on"] = date.today().isoformat()
            df.to_csv(out, index=False, encoding="utf-8")
            print(f"  fetched: PTF {y} ({len(df)} rows, {chunks[0][0]} – {chunks[1][1]})")
        paths.append(out)
    return paths


def load_ptf(paths) -> pd.DataFrame:
    df = pd.concat([pd.read_csv(p) for p in paths], ignore_index=True)
    # "2016-01-01T00:00:00+02:00": first 10 characters are the local date, the hour is in a separate column
    df["local_date"] = pd.to_datetime(df["date"].str[:10])
    df = df.drop_duplicates(subset=["date"]).sort_values("date")
    df["year"] = df["local_date"].dt.year
    df["month"] = df["local_date"].dt.month
    return df[df["year"].isin(YEARS)].reset_index(drop=True)


# ---------------------------------------------------------------- FX
def monthly_usd_rate() -> pd.DataFrame:
    evds = _load_evds_module()
    paths = [RAW_DIR / f"evds_{EVDS_USD}_{y}.json" for y in YEARS]
    missing = [p.name for p in paths if not p.exists()]
    if missing:
        sys.exit(f"EVDS raw files missing: {missing} (run src/05_fetch_evds.py first).")
    fx = evds.load_series(paths, EVDS_USD).dropna(subset=["value"])
    fx["d"] = pd.to_datetime(fx["date_str"], format="%d-%m-%Y")
    fx["year"], fx["month"] = fx["d"].dt.year, fx["d"].dt.month
    return (fx.groupby(["year", "month"])
              .agg(usd_try=("value", "mean"), fx_days=("value", "size"))
              .reset_index())


# ---------------------------------------------------------------- weight source
def electricity_weights(master: pd.DataFrame) -> pd.DataFrame:
    """electricity_weight = electricity_consumption / total_energy_consumption (view = own).
    For years without a value the nearest year's weight is used (the later year on ties); the source is in 'weight_source'."""
    own = master[master["view"] == "own"].set_index("year").sort_index()
    w = (own["electricity_consumption"] / own["total_energy_consumption"]).reindex(YEARS)
    esg_src = own["esg_source"].reindex(YEARS)
    have = [y for y in YEARS if pd.notna(w.loc[y])]
    rows = []
    for y in YEARS:
        if y in have:
            src_year, how = y, "same year"
        else:
            src_year = min(have, key=lambda h: (abs(h - y), -h))
            how = f"no data; nearest year ({src_year}) used"
        s = str(esg_src.loc[src_year])
        restated = [k for k in ("electricity_consumption", "total_energy_consumption") if f"{k}(" in s]
        rep = "; ".join(f"{k} {s.split(k + '(')[1].split(')')[0]}" for k in restated) or "own report"
        rows.append({"year": y, "electricity_weight": w.loc[src_year], "weight_year": src_year,
                     "weight_source": f"{how}; annual_master own {src_year}: {rep}"})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- table and checks
def build(coal, ptf, fx):
    coal_y = coal.groupby("year")[list(COAL_COLS.values())].mean()
    ptf_m = ptf.groupby(["year", "month"])["price"].mean().rename("ptf_try_mwh").reset_index()
    ptf_m = ptf_m.merge(fx, on=["year", "month"], how="left")
    ptf_m["ptf_usd_mwh"] = ptf_m["ptf_try_mwh"] / ptf_m["usd_try"]
    ptf_y = ptf_m.groupby("year")[["ptf_try_mwh", "ptf_usd_mwh"]].mean()
    out = coal_y.join(ptf_y).reset_index()
    out.insert(1, "basis", "nominal")
    out["source"] = ("Coal: World Bank CMO (Pink Sheet) monthly, simple annual average; "
                     "PTF: EPİAŞ hourly, monthly average / EVDS monthly average USD buying rate, simple annual average")
    return out, ptf_m


def build_checks(coal, ptf, ptf_m, weights):
    rows = []
    # 1) PTF missing hours / days
    for y in YEARS:
        g = ptf[ptf["year"] == y]
        days_expected = (date(y + 1, 1, 1) - date(y, 1, 1)).days
        hours_per_day = g.groupby("local_date").size()
        days_missing = days_expected - hours_per_day.size
        hours_expected = days_expected * 24
        n_nan = int(g["price"].isna().sum())
        odd = hours_per_day[hours_per_day != 24]
        detail = (f"{len(g)} hours (expected {hours_expected}); {hours_per_day.size}/{days_expected} days; "
                  f"empty prices {n_nan}")
        if len(odd):
            detail += "; days with other than 24 hours: " + ", ".join(f"{d.date()}={n}" for d, n in odd.items())
        ok = days_missing == 0 and n_nan == 0 and len(g) - hours_expected in (-1, 0, 1)
        rows.append({"check": "ptf_missing_hours_days", "year": y, "status": "passed" if ok else "warning",
                     "value": hours_expected - len(g), "detail": detail})
    # 2) Monthly FX matching
    for y in YEARS:
        g = ptf_m[ptf_m["year"] == y]
        miss = g[g["usd_try"].isna()]["month"].tolist()
        months_ptf = g["month"].nunique()
        rows.append({"check": "fx_monthly_match", "year": y, "status": "passed" if not miss and months_ptf == 12 else "warning",
                     "value": len(miss) + (12 - months_ptf),
                     "detail": f"PTF months {months_ptf}; months without an FX rate: {miss or 'none'}; "
                               f"FX days per month {int(g['fx_days'].min())}–{int(g['fx_days'].max())}"})
    # 3) Coal missing months
    for y in YEARS:
        g = coal[coal["year"] == y]
        for c in COAL_COLS.values():
            n = int(g[c].notna().sum())
            rows.append({"check": "coal_missing_months", "year": y, "status": "passed" if n == 12 else "warning",
                         "value": 12 - n, "detail": f"{c}: {n}/12 months"})
    # 4) Cross-check: EPİAŞ's own USD price (priceUsd) vs the EVDS conversion
    if "priceUsd" in ptf.columns:
        epias_usd = ptf.groupby(["year", "month"])["priceUsd"].mean().groupby("year").mean()
        ours = ptf_m.groupby("year")["ptf_usd_mwh"].mean()
        for y in YEARS:
            diff = (ours.loc[y] / epias_usd.loc[y] - 1) * 100
            rows.append({"check": "ptf_usd_vs_epias_priceUsd", "year": y, "status": "info",
                         "value": round(diff, 3),
                         "detail": f"EVDS conversion {ours.loc[y]:.2f} vs EPİAŞ priceUsd average {epias_usd.loc[y]:.2f} USD/MWh (difference %)"})
    # 5) Weight source
    for _, r in weights.iterrows():
        rows.append({"check": "electricity_weight_source", "year": int(r["year"]),
                     "status": "info" if r["weight_year"] == r["year"] else "warning",
                     "value": round(r["electricity_weight"], 5), "detail": r["weight_source"]})
    # 6) Series notes (from the World Bank Description sheet)
    rows.append({"check": "coal_series_note", "year": None, "status": "info", "value": None,
                 "detail": "Coal, Australian: from February 2022 the Newcastle 6000 kcal/kg futures price; spot before that."})
    rows.append({"check": "coal_series_note", "year": None, "status": "info", "value": None,
                 "detail": "Coal, South African: June–November 2022, some months of 2023 and January–May 2024 are World Bank "
                           "estimates based on similar indicators."})
    out = pd.DataFrame(rows)
    out["year"] = out["year"].astype("Int64")
    return out


def main():
    refresh = "--refresh" in sys.argv
    coal = load_coal()
    ptf = load_ptf(fetch_ptf(refresh))
    fx = monthly_usd_rate()
    out, ptf_m = build(coal, ptf, fx)
    weights = electricity_weights(pd.read_csv(MASTER_IN))
    checks = build_checks(coal, ptf, ptf_m, weights)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, index=False, encoding="utf-8")
    CHECK_OUT.parent.mkdir(parents=True, exist_ok=True)
    checks.to_csv(CHECK_OUT, index=False, encoding="utf-8")
    print(out.drop(columns="source").round(2).to_string(index=False))
    print(f"Table: {OUT.relative_to(ROOT)} ({len(out)} rows)")
    print(f"Check: {CHECK_OUT.relative_to(ROOT)} ({len(checks)} rows; warnings {int((checks['status'] == 'warning').sum())})")


if __name__ == "__main__":
    main()
