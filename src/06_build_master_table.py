"""
06 – Builds a single annual master table for the analysis.

Inputs (output/tables/):
  cost_items_annual.csv, financials_annual.csv  -> financial items
  esg_kpis_annual.csv                           -> physical indicators (own rows)
  esg_kpis_candidates.csv                       -> physical value from the same report for restated rows
                                                   (with src/03's classification/unit conversion steps)
  fx_rates_annual.csv                           -> EVDS FX rates (USD equivalents)
Outputs:
  output/tables/annual_master.csv
  output/tables/annual_master_dictionary.csv
  output/checks/annual_master_checks.csv

Row = each (year, restated, report_year) in the financial tables. view = own / restated_in_YYYY.
FX rule: basis nominal -> EVDS annual average (year); basis tms29 -> EVDS year-end rate (report_year;
because IAS 29 amounts are stated in the purchasing power at the report date).
A derived indicator whose numerator or denominator is missing is left blank.
"""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
T = ROOT / "output" / "tables"
MASTER_OUT = T / "annual_master.csv"
DICT_OUT = T / "annual_master_dictionary.csv"
CHECK_OUT = ROOT / "output" / "checks" / "annual_master_checks.csv"

# src/03's classification and unit conversion functions (for restated rows)
_spec = importlib.util.spec_from_file_location("esg03", ROOT / "src" / "03_build_esg_kpis_annual.py")
esg03 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(esg03)

# Physical indicators included in the master table (old-definition / unlabeled items excluded)
ESG_ITEMS = [
    "total_energy_consumption", "clinker_energy_consumption", "thermal_energy_conventional",
    "electricity_consumption", "self_generated_renewable_electricity", "renewable_energy_consumption",
    "irec_mwh", "renewable_electricity_share", "alt_fuel_rate", "alt_fuel_rate_grey", "alt_fuel_rate_white",
    "specific_heat_consumption", "clinker_production", "cement_production_total", "grey_cement_production",
    "white_cement_production", "cac_production", "ready_mix_production",
]
SEGMENTS = ["cement", "readymix", "building_materials"]


# ---------------------------------------------------------------- financial part

def report_year_of(path: str) -> int:
    return int(Path(path).parts[-2])


def load_financials() -> tuple[pd.DataFrame, list]:
    ci = pd.read_csv(T / "cost_items_annual.csv")
    fa = pd.read_csv(T / "financials_annual.csv")
    issues = []
    # Revenue is in both tables; it must be the same
    m = ci[ci["item"] == "revenue"].merge(fa[fa["item"] == "revenue"], on=["year", "restated", "source_file"],
                                          suffixes=("_ci", "_fa"))
    bad = m[m["value_ci"] != m["value_fa"]]
    for _, b in bad.iterrows():
        issues.append({"check": "revenue_consistency", "year": b["year"], "view": "", "indicator": "revenue",
                       "status": "mismatch", "detail": f"cost_items {b['value_ci']} vs financials {b['value_fa']}"})
    fa = fa[fa["item"] != "revenue"]
    both = pd.concat([ci, fa], ignore_index=True)
    both["report_year"] = both["source_file"].map(report_year_of)
    wide = both.pivot_table(index=["year", "restated", "report_year", "basis"], columns="item", values="value",
                            aggfunc="first").reset_index()
    wide.columns.name = None
    wide["view"] = np.where(~wide["restated"], "own", "restated_in_" + wide["report_year"].astype(str))
    return wide, issues


# ---------------------------------------------------------------- physical part

def attach_esg(master: pd.DataFrame) -> tuple[pd.DataFrame, list]:
    annual = pd.read_csv(T / "esg_kpis_annual.csv")
    cand = esg03.add_derived_totals(esg03.prepare(pd.read_csv(T / "esg_kpis_candidates.csv")))
    notes = []
    src_col, flag_col = [], []
    values = {it: [] for it in ESG_ITEMS}
    for _, row in master.iterrows():
        y, ry = int(row["year"]), int(row["report_year"])
        from_restated, flags = [], []
        for it in ESG_ITEMS:
            v = np.nan
            if not row["restated"]:
                own = annual[(annual["item"] == it) & (annual["year"] == y) & (~annual["restated"])]
                if len(own):
                    v, q = own.iloc[0]["value"], own.iloc[0]["quality"]
                else:
                    rst = annual[(annual["item"] == it) & (annual["year"] == y) & (annual["restated"])]
                    if len(rst):
                        v, q = rst.iloc[0]["value"], rst.iloc[0]["quality"]
                        from_restated.append(f"{it}({int(rst.iloc[0]['report_year'])} report)")
                        notes.append({"check": "esg_from_restated", "year": y, "view": row["view"],
                                      "indicator": it, "status": "restated value used",
                                      "detail": f"no value in own report; value from the {int(rst.iloc[0]['report_year'])} "
                                                f"report ({rst.iloc[0]['value']:,.2f})"})
            else:
                g = cand[(cand["item"] == it) & (cand["year"] == y) & (cand["report_year"] == ry)]
                p = esg03.pick(g) if len(g) else None
                if p is not None:
                    v, q = p["value"], p["quality"]
            if not np.isnan(v) and q != "ok":
                flags.append(f"{it}={q}")
            values[it].append(v)
        if row["restated"]:
            src_col.append(f"same-year values from the {ry} report (unmatched items blank)")
        else:
            src_col.append("own" + ("; restated: " + ", ".join(from_restated) if from_restated else ""))
        flag_col.append("; ".join(flags))
    for it in ESG_ITEMS:
        master[it] = values[it]
    master["esg_source"] = src_col
    master["esg_quality_flags"] = flag_col
    return master, notes


# ---------------------------------------------------------------- derived indicators

def fx_lookup():
    fx = pd.read_csv(T / "fx_rates_annual.csv")
    fy = fx[fx["period"] == "FY"]
    return {(r["item"], int(r["year"])): r["value"] for _, r in fy.iterrows()}


def div(a, b):
    return a / b if pd.notna(a) and pd.notna(b) and b != 0 else np.nan


# Derived indicators: name -> (function, inputs, unit, description, formula)
def derived_specs():
    specs = {
        "gross_margin": (lambda r: div(r["revenue"] - r["cost_of_sales"], r["revenue"]),
                         ["revenue", "cost_of_sales"], "ratio", "Gross margin", "(revenue − cost_of_sales) / revenue"),
        "energy_share_production_cost": (lambda r: div(r["energy_cost"], r["total_production_cost"]),
                                         ["energy_cost", "total_production_cost"], "ratio",
                                         "Share of energy cost in total production cost",
                                         "energy_cost / total_production_cost"),
        "energy_to_revenue": (lambda r: div(r["energy_cost"], r["revenue"]), ["energy_cost", "revenue"], "ratio",
                              "Energy cost to revenue", "energy_cost / revenue"),
        "export_share": (lambda r: div(r["revenue_export"], r["revenue_domestic"] + r["revenue_export"]),
                         ["revenue_export", "revenue_domestic"], "ratio",
                         "Share of export sales in gross sales (before deductions)",
                         "revenue_export / (revenue_domestic + revenue_export)"),
        "ebitda_proxy": (lambda r: r["operating_profit"] + r["depreciation_amortization_total"]
                         if pd.notna(r["operating_profit"]) and pd.notna(r["depreciation_amortization_total"]) else np.nan,
                         ["operating_profit", "depreciation_amortization_total"], "TRY",
                         "EBITDA proxy (operating profit + depreciation adjustment in the cash flow statement)",
                         "operating_profit + depreciation_amortization_total"),
        "net_debt": (lambda r: r["financial_debt_total"] - r["cash"]
                     if pd.notna(r["financial_debt_total"]) and pd.notna(r["cash"]) else np.nan,
                     ["financial_debt_total", "cash"], "TRY", "Net financial debt (incl. lease liabilities)",
                     "financial_debt_total − cash"),
        "net_debt_to_ebitda": (lambda r: div(r["net_debt"], r["ebitda_proxy"]), ["net_debt", "ebitda_proxy"], "multiple",
                               "Net debt / EBITDA proxy", "net_debt / ebitda_proxy"),
        "implied_energy_price_try_per_mwh": (lambda r: div(r["energy_cost"], r["total_energy_consumption"]),
                                             ["energy_cost", "total_energy_consumption"], "TRY/MWh",
                                             "Implied energy price", "energy_cost / total_energy_consumption"),
        "energy_cost_per_ton_clinker": (lambda r: div(r["energy_cost"], r["clinker_production"]),
                                        ["energy_cost", "clinker_production"], "TRY/ton",
                                        "Energy cost per ton of clinker", "energy_cost / clinker_production"),
        "electricity_share_proxy": (lambda r: div(r["enerjisa_electricity_purchases"], r["energy_cost"]),
                                    ["enerjisa_electricity_purchases", "energy_cost"], "ratio",
                                    "Enerjisa electricity purchases to energy cost (rough indicator of the electricity share)",
                                    "enerjisa_electricity_purchases / energy_cost"),
    }
    for s in SEGMENTS:
        specs[f"segment_{s}_gross_margin"] = (
            (lambda s: lambda r: div(r.get(f"segment_{s}_gross_profit"), r.get(f"segment_{s}_revenue")))(s),
            [f"segment_{s}_gross_profit", f"segment_{s}_revenue"], "ratio", f"{s} segment gross margin",
            f"segment_{s}_gross_profit / segment_{s}_revenue")
        specs[f"segment_{s}_revenue_share"] = (
            (lambda s: lambda r: div(r.get(f"segment_{s}_revenue"), r["_segment_revenue_sum"]))(s),
            [f"segment_{s}_revenue", "_segment_revenue_sum"], "ratio",
            f"{s} segment revenue share (relative to the sum of operating segments before elimination)",
            f"segment_{s}_revenue / (cement + readymix + building_materials segment revenues)")
    return specs


def usd_specs():
    return {
        "revenue_usd": ("revenue", "USD"),
        "gross_profit_usd": ("_gross_profit", "USD"),
        "energy_cost_usd": ("energy_cost", "USD"),
        "ebitda_proxy_usd": ("ebitda_proxy", "USD"),
        "implied_energy_price_usd_per_mwh": ("implied_energy_price_try_per_mwh", "USD/MWh"),
    }


def add_derived(m: pd.DataFrame) -> tuple[pd.DataFrame, list]:
    for c in ["segment_building_materials_revenue", "segment_building_materials_gross_profit"]:
        if c not in m.columns:
            m[c] = np.nan
    seg_cols = [f"segment_{s}_revenue" for s in SEGMENTS]
    m["_segment_revenue_sum"] = m[seg_cols].sum(axis=1, min_count=1)
    m["_gross_profit"] = m["revenue"] - m["cost_of_sales"]
    issues = []
    specs = derived_specs()
    for name, (fn, inputs, *_rest) in specs.items():
        m[name] = m.apply(fn, axis=1)
        for _, r in m[m[name].isna()].iterrows():
            missing = [i for i in inputs if pd.isna(r.get(i))]
            if name.startswith("segment_building_materials") and r["report_year"] < 2024:
                continue  # the building materials segment does not exist before the 2024 report
            reason = ("missing input: " + ", ".join(missing)) if missing else "denominator is zero"
            issues.append({"check": "derived_not_computed", "year": r["year"], "view": r["view"],
                           "indicator": name, "status": "not computed", "detail": reason})
    # USD equivalents
    fx = fx_lookup()
    rates, rate_notes = [], []
    for _, r in m.iterrows():
        if r["basis"] == "nominal":
            rate = fx.get(("fx_usd_try_average", int(r["year"])))
            rate_notes.append(f"EVDS USD/TRY {int(r['year'])} annual average")
        else:
            rate = fx.get(("fx_usd_try_period_end", int(r["report_year"])))
            rate_notes.append(f"EVDS USD/TRY {int(r['report_year'])} year-end (IAS 29: purchasing power at report date)")
        rates.append(rate if rate is not None else np.nan)
    m["usd_try_rate_used"] = rates
    m["usd_rate_basis"] = rate_notes
    for name, (src, _unit) in usd_specs().items():
        m[name] = [div(a, b) for a, b in zip(m[src], m["usd_try_rate_used"])]
        for _, r in m[m[name].isna()].iterrows():
            miss = [x for x in [src, "usd_try_rate_used"] if pd.isna(r.get(x))]
            issues.append({"check": "derived_not_computed", "year": r["year"], "view": r["view"], "indicator": name,
                           "status": "not computed", "detail": "missing input: " + ", ".join(miss)})
    return m, issues


# ---------------------------------------------------------------- dictionary

def build_dictionary(m: pd.DataFrame) -> pd.DataFrame:
    rows = []
    fa = pd.read_csv(T / "financials_annual.csv")
    ci = pd.read_csv(T / "cost_items_annual.csv")
    esg = pd.read_csv(T / "esg_kpis_annual.csv")
    unit_of = {**dict(zip(ci["item"], ci["unit"])), **dict(zip(fa["item"], fa["unit"])),
               **dict(zip(esg["item"], esg["unit"]))}
    key_desc = {
        "year": ("Year the value belongs to", "", "", "all financial tables"),
        "restated": ("True: comparative column of the following report; False: the year's own report", "", "", "financial tables"),
        "report_year": ("Year of the financial report the values were taken from", "", "", "financial tables (source_file)"),
        "basis": ("nominal (2016–2022 reports) / tms29 (2023+ reports; purchasing power at report date)", "", "", "financial tables"),
        "view": ("own = own report; restated_in_YYYY = comparative column of the YYYY report", "", "", "derived"),
        "esg_source": ("Source of the physical values; for own rows, restated items used where the own report has none are listed",
                       "", "", "esg_kpis_annual / esg_kpis_candidates"),
        "esg_quality_flags": ("Physical values whose quality is not 'ok' (suspect / unit_corrected / definition_break)",
                              "", "", "esg_kpis_annual"),
        "usd_try_rate_used": ("FX rate used for USD equivalents", "nominal: annual average (year); tms29: year-end (report_year)",
                              "TRY/USD", "fx_rates_annual (EVDS)"),
        "usd_rate_basis": ("Description of the FX rate used", "", "", "derived"),
    }
    specs = derived_specs()
    usd = usd_specs()
    fin_items = set(ci["item"]) | set(fa["item"])
    for col in m.columns:
        if col in key_desc:
            d, f, u, s = key_desc[col]
        elif col in specs:
            _, _, u, d, f = specs[col]
            s = "derived"
        elif col in usd:
            src, u = usd[col]
            src_txt = "revenue − cost_of_sales" if src == "_gross_profit" else src
            d, f, s = f"{src_txt} in USD", f"({src_txt}) / usd_try_rate_used", "derived (EVDS rate)"
        elif col in ESG_ITEMS:
            d, f, u = f"Physical indicator: {col}", "", unit_of.get(col, "")
            s = "esg_kpis_annual.csv (own) / esg_kpis_candidates.csv (restated)"
        elif col in fin_items:
            d, f, u = f"Financial item: {col}", "", unit_of.get(col, "")
            s = "cost_items_annual.csv" if col in set(ci["item"]) else "financials_annual.csv"
        else:
            d, f, u, s = "", "", "", ""
        rows.append({"column": col, "description": d, "formula": f, "unit": u, "source_table": s})
    return pd.DataFrame(rows)


def main():
    master, issues = load_financials()
    master, esg_notes = attach_esg(master)
    master, d_issues = add_derived(master)
    master = master.drop(columns=["_segment_revenue_sum", "_gross_profit"])
    key = ["year", "view", "restated", "report_year", "basis"]
    master = master[key + [c for c in master.columns if c not in key]]
    master = master.sort_values(["year", "restated"]).reset_index(drop=True)
    MASTER_OUT.parent.mkdir(parents=True, exist_ok=True)
    master.to_csv(MASTER_OUT, index=False, encoding="utf-8")

    dic = build_dictionary(master)
    dic.to_csv(DICT_OUT, index=False, encoding="utf-8")

    checks = pd.DataFrame(issues + esg_notes + d_issues,
                          columns=["check", "year", "view", "indicator", "status", "detail"])
    CHECK_OUT.parent.mkdir(parents=True, exist_ok=True)
    checks.to_csv(CHECK_OUT, index=False, encoding="utf-8")

    print(f"Master table: {MASTER_OUT.relative_to(ROOT)} ({len(master)} rows, {len(master.columns)} columns)")
    print(f"Dictionary  : {DICT_OUT.relative_to(ROOT)} ({len(dic)} column descriptions)")
    print(f"Check       : {CHECK_OUT.relative_to(ROOT)} ({len(checks)} rows)")
    print(checks.groupby(["check", "indicator"]).size().to_string())


if __name__ == "__main__":
    main()
