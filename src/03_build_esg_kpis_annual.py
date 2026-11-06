"""
03 – Builds the clean annual indicator table used in the analysis from the ESG candidate table.

Input  : output/tables/esg_kpis_candidates.csv (output of src/02; this script does not modify it)
Outputs:
  output/tables/esg_kpis_annual.csv        (indicator × year; primary + restated values)
  output/checks/esg_kpis_annual_checks.csv (primary value available by year, year-on-year changes above 30%)

Selection rules (summary):
1. The primary value comes from the year's own report (report_year == year) -> restated = False.
   If the following year's report gives a different value for the same year, it is a separate row -> restated = True.
2. Units: energy -> MWh, production -> ton (ready-mix concrete -> m3), ratios -> percentage points.
3. The reports' own unit errors are listed in UNIT_FIXES; quality = unit_corrected.
4. Malformed numbers are not guessed; if unreadable in the own report, the clean value of the following report is used with restated = True.
5. Old "total energy" values in kWh are a separate item: total_energy_consumption_old_definition.
6. Alternative fuel lines without a grey/white split: alt_fuel_rate_unlabeled.
7. Clinker / cement group total: if the report has no total, the sum of grey + white (+ CAC) from the same report (stated in the method column).
8. Suspect values are listed in SUSPECT; they are kept in the table with quality = suspect.
"""
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CAND_IN = ROOT / "output" / "tables" / "esg_kpis_candidates.csv"
TABLE_OUT = ROOT / "output" / "tables" / "esg_kpis_annual.csv"
CHECK_OUT = ROOT / "output" / "checks" / "esg_kpis_annual_checks.csv"

YEARS = range(2016, 2026)
RESTATE_TOL = 0.005   # a restated row is added if the following report's value differs by more than 0.5%
YOY_FLAG = 30.0       # year-on-year change threshold (%)

ENERGY_ITEMS = {
    "total_energy_consumption", "total_energy_consumption_old_definition", "clinker_energy_consumption",
    "thermal_energy_conventional", "thermal_energy_alternative", "electricity_consumption",
    "self_generated_renewable_electricity", "self_generated_whr", "self_generated_solar",
    "renewable_energy_consumption", "irec_mwh",
}
ENERGY_FACTORS = {  # -> MWh
    "mwh": 1.0, "mwh/yil": 1.0, "mwh/yr": 1.0, "bin mwh": 1e3, "milyon mwh": 1e6,
    "tj": 277.78, "tj/yil": 277.78, "gj": 0.27778, "kwh": 0.001,
}
MASS_FACTORS = {"ton": 1.0, "bin ton": 1e3, "milyon ton": 1e6}      # -> ton
VOLUME_FACTORS = {"m3": 1.0, "milyon m3": 1e6}                      # -> m3

# The reports' own unit errors: (item, report_year, stated unit) -> (unit to apply, explanation)
UNIT_FIXES = {
    ("thermal_energy_conventional", 2024, "mwh"):
        ("tj", "report states 'MWh' but the value is TJ-sized (the same values are TJ in earlier reports); treated as TJ"),
    ("self_generated_whr", 2016, "tj"):
        ("gj", "report states 'TJ' but the value is GJ-sized (consistent with the MWh value in the same table); treated as GJ"),
}
# The waste heat "TJ/yıl" lines in the 2017–2020 reports have the same error (e.g. 51,911 MWh = 186,880 GJ)
for _ry in range(2017, 2021):
    UNIT_FIXES[("self_generated_whr", _ry, "tj/yil")] = (
        "gj", "report states 'TJ/yıl' (TJ/year) but the value is GJ-sized (consistent with the MWh/year value in the same table); treated as GJ")

# Suspect values: (item, report_year, source_page) -> explanation
SUSPECT = {
    ("white_cement_production", 2023, 88):
        "white cement production is ~1.2–1.4 million tons in other years; the sales table on the same page shows 1.59 / 1.72 million tons",
    ("alt_fuel_rate_unlabeled_row1", 2020, 126):
        "a footnote digit may be attached at the end ('7,821'); the 2021 report gives 7.82 for 2020 grey",
    ("alt_fuel_rate_unlabeled_row2", 2020, 126):
        "a footnote digit may be attached at the end ('6,102'); the 2021 report gives 6.10 for 2020 grey+white",
}

# Verified explanatory notes (quality unchanged): (item, report_year, year) -> note
VERIFIED_NOTES = {
    ("alt_fuel_rate_grey", 2024, 2024):
        "the report states it was reduced for economic reasons (2024 report, around p.98)",
}

# Items whose total is derived from components: total -> (required components, optional components)
DERIVED_TOTALS = {
    "clinker_production": (["clinker_production_grey", "clinker_production_white"], ["clinker_production_cac"]),
    "cement_production_total": (["grey_cement_production", "white_cement_production"], ["cac_production"]),
}

COLUMNS = ["item", "year", "value", "unit", "restated", "report_year", "source_file", "source_page",
           "original_value", "original_unit", "scope", "method", "quality", "note"]


# ---------------------------------------------------------------- classification

def classify(r):
    """Maps a candidate row to an analysis item: (item, quality, notes)."""
    item, scope, raw = r["item"], str(r["scope"]), str(r["raw_text"]).lower()
    unit = str(r["unit"]).lower()
    notes, quality = [], "ok"
    if item == "total_energy_consumption":
        if unit == "kwh":
            item, quality = "total_energy_consumption_old_definition", "definition_break"
            notes.append("old definition in kWh (far below the MWh series; probably electricity only / partial scope)")
        elif "clinker production" in scope:
            item = "clinker_energy_consumption"
    elif item == "alt_fuel_rate" and scope == "unspecified":
        if r["year_uncertain"]:
            item = "alt_fuel_rate_unlabeled"  # single value in narrative / summary box
        else:
            # The same table has two unlabeled lines (probably grey and white); kept apart by order
            item = f"alt_fuel_rate_unlabeled_row{int(r['row_order']) + 1}"
            notes.append(f"unlabeled line no. {int(r['row_order']) + 1} in the table; not assigned to grey/white")
    elif item == "renewable_electricity_share":
        if "tüketimi ve üretimi" in raw:
            item, quality = "renewable_energy_share_old_definition", "definition_break"
            notes.append("'Yenilenebilir Enerji Tüketimi ve Üretimi' (renewable energy consumption and production) ratio; not the electricity share, different definition")
        elif "location-based" in scope:
            item = "renewable_electricity_share_location"
        elif scope == "unspecified":
            notes.append("market-/location-based split not stated")
    elif item == "self_generated_renewable_electricity":
        if "waste heat" in scope:
            item = "self_generated_whr"
        elif "solar" in scope:
            item = "self_generated_solar"
    elif item == "clinker_production":
        for key, name in [("grey cement", "clinker_production_grey"), ("white cement", "clinker_production_white"),
                          ("CAC", "clinker_production_cac")]:
            if scope.startswith(key):
                item = name
    return item, quality, notes


def standardize(item, value, unit, report_year):
    """Returns (standard value, standard unit, method note, quality, note)."""
    u = str(unit).lower()
    quality, note = None, ""
    fix = UNIT_FIXES.get((item, report_year, u))
    if fix:
        u, note = fix
        quality = "unit_corrected"
    if item in ENERGY_ITEMS:
        f, std = ENERGY_FACTORS.get(u), "MWh"
    elif item.endswith("_production") or item.startswith("clinker_production") or item == "cement_production_total":
        if item == "ready_mix_production":
            f, std = VOLUME_FACTORS.get(u), "m3"
        else:
            f, std = MASS_FACTORS.get(u), "ton"
    elif u == "%":
        f, std = 1.0, "%"
    elif u == "mj/ton klinker":
        f, std = 1.0, "MJ/ton_clinker"
    elif u == "kwh/ton":
        f, std = 1.0, "kWh/ton_clinker"
    else:
        f, std = None, u
    if f is None:
        return np.nan, std, f"unit not recognized ({unit})", "suspect", note
    method = "" if f == 1.0 else f"{u} -> {std} (x{f:g})"
    v = value * f if pd.notna(value) else np.nan
    return v, std, method, quality, note


def prepare(cand: pd.DataFrame) -> pd.DataFrame:
    # Row order within the same report-page-year (the candidate table keeps the row order)
    cand = cand.copy()
    cand["row_order"] = cand.groupby(["item", "scope", "year", "report_year", "source_page"]).cumcount()
    d = cand.dropna(subset=["year"]).copy()
    d["year"] = d["year"].astype(int)
    rows = []
    for _, r in d.iterrows():
        item, quality, notes = classify(r)
        v, std, conv, q2, fix_note = standardize(item, r["value"], r["unit"], int(r["report_year"]))
        if q2:
            quality = q2 if quality == "ok" else quality
        vnote = VERIFIED_NOTES.get((item, int(r["report_year"]), int(r["year"])))
        if vnote:
            notes.append(vnote)
        susp = SUSPECT.get((item, int(r["report_year"]), int(r["source_page"])))
        if susp:
            quality = "suspect"
            notes.append(susp)
        if fix_note:
            notes.append(fix_note)
        if r["year_uncertain"]:
            notes.append("year assumed (single-value summary/SASB line)")
        if pd.isna(r["value"]):
            notes.append(f"number malformed/unreadable in report: {r['note']}")
        rows.append({
            "item": item, "year": r["year"], "value": v, "unit": std,
            "report_year": int(r["report_year"]), "source_file": r["source_file"],
            "source_page": str(int(r["source_page"])), "original_value": r["value"],
            "original_unit": r["unit"], "scope": r["scope"], "method": conv, "quality": quality,
            "note": "; ".join(notes), "certain": not r["year_uncertain"],
            "native_unit": str(r["unit"]).lower() in ("mwh", "mwh/yil", "mwh/yr", "ton", "m3", "%",
                                                       "mj/ton klinker", "kwh/ton"),
            "scope_known": r["scope"] != "unspecified",
            "raw_text": r["raw_text"],
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- totals

def pick(g: pd.DataFrame):
    """Picks the best clean value among the candidates of one report (year certain > scope labelled > native unit > page)."""
    g = g.dropna(subset=["value"])
    if g.empty:
        return None
    g = g.assign(_p=g["source_page"].str.split(",").str[0].astype(int))
    g = g.sort_values(["certain", "scope_known", "native_unit", "_p"], ascending=[False, False, False, True])
    return g.iloc[0]


def add_derived_totals(d: pd.DataFrame) -> pd.DataFrame:
    """If the report has no total, builds a total candidate from the same report's components."""
    new = []
    for total, (req, opt) in DERIVED_TOTALS.items():
        comps = d[d["item"].isin(req + opt)]
        for (ry, yr), g in comps.groupby(["report_year", "year"]):
            parts = {it: pick(g[g["item"] == it]) for it in req + opt}
            if any(parts[it] is None for it in req):
                continue
            used = [parts[it] for it in req + opt if parts[it] is not None]
            s = sum(p["value"] for p in used)
            explicit = d[(d["item"] == total) & (d["report_year"] == ry) & (d["year"] == yr)].dropna(subset=["value"])
            comp_txt = " + ".join(f"{p['item']}={p['value']:,.0f}" for p in used)
            if not explicit.empty:
                # the report has a total: no derivation, only a consistency note
                ex = pick(explicit)
                if abs(ex["value"] / s - 1) > 0.01:
                    idx = explicit.index
                    d.loc[idx, "note"] = d.loc[idx, "note"].apply(
                        lambda n: "; ".join(filter(None, [n, f"sum of components in the same report differs: {s:,.0f} ({comp_txt})"])))
                continue
            missing = [it for it in opt if parts[it] is None]
            quality = "suspect" if any(p["quality"] == "suspect" for p in used) else "ok"
            new.append({
                "item": total, "year": yr, "value": s, "unit": used[0]["unit"], "report_year": ry,
                "source_file": used[0]["source_file"],
                "source_page": ",".join(sorted({p["source_page"] for p in used}, key=int)),
                "original_value": np.nan, "original_unit": "calculated", "scope": "sum of components",
                "method": "no total in report; sum of " + " + ".join(p["item"] for p in used) + " from the same report",
                "quality": quality,
                "note": "; ".join(filter(None, [comp_txt, f"missing component: {', '.join(missing)}" if missing else "",
                                               "at least one component is suspect" if quality == "suspect" else ""])),
                "certain": all(p["certain"] for p in used), "native_unit": True, "scope_known": True,
                "raw_text": "",
            })
    return pd.concat([d, pd.DataFrame(new)], ignore_index=True) if new else d


# ---------------------------------------------------------------- selection

def select(d: pd.DataFrame) -> pd.DataFrame:
    out = []
    for (item, yr), g in d.groupby(["item", "year"]):
        own_all = g[g["report_year"] == yr]
        nxt_all = g[g["report_year"] == yr + 1]
        own, nxt = pick(own_all), pick(nxt_all)

        if own is not None:
            row = own.to_dict()
            row["restated"] = False
            row["method"] = "; ".join(filter(None, ["own report", row["method"]]))
            others = own_all.dropna(subset=["value"])
            diff = others[(others["value"] - own["value"]).abs() > RESTATE_TOL * abs(own["value"])]
            if not diff.empty:
                alt = ", ".join(f"{v:,.2f} (s.{p})" for v, p in zip(diff["value"], diff["source_page"]))
                row["note"] = "; ".join(filter(None, [row["note"], f"the same report also has a different value: {alt}"]))
            out.append(row)
            if nxt is not None and abs(nxt["value"] - own["value"]) > RESTATE_TOL * abs(own["value"]):
                r2 = nxt.to_dict()
                r2["restated"] = True
                r2["method"] = "; ".join(filter(None, ["restated value from the following report", r2["method"]]))
                out.append(r2)
        elif nxt is not None:
            r2 = nxt.to_dict()
            r2["restated"] = True
            if not own_all.empty:
                raw = "; ".join(own_all["raw_text"].astype(str).unique())
                why = f"value in own report unreadable (raw line: {raw})"
            elif yr + 1 == min(YEARS):
                why = "own report out of scope (2015)"
            else:
                why = "no value for this year in own report"
            r2["method"] = "; ".join(filter(None, [f"{why}; value from the following report used", r2["method"]]))
            out.append(r2)
    res = pd.DataFrame(out)
    res["year"] = res["year"].astype(int)
    res["report_year"] = res["report_year"].astype(int)
    return res[COLUMNS].sort_values(["item", "year", "restated"]).reset_index(drop=True)


# ---------------------------------------------------------------- checks

def build_checks(t: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for item, g in t.groupby("item"):
        prev = None
        for yr in YEARS:
            own = g[(g["year"] == yr) & (~g["restated"])]
            alt = g[(g["year"] == yr) & (g["restated"])]
            if not own.empty:
                used, src = own.iloc[0], "own report"
            elif not alt.empty:
                used, src = alt.iloc[0], f"{int(alt.iloc[0]['report_year'])} report (restated)"
            else:
                used, src = None, "none"
            val = used["value"] if used is not None else np.nan
            yoy = (val / prev - 1) * 100 if (prev not in (None, 0) and pd.notna(prev) and pd.notna(val)) else np.nan
            notes = []
            if used is not None and used["quality"] != "ok":
                notes.append(f"quality = {used['quality']}")
            if used is not None and "sum of components" == used["scope"]:
                notes.append("total calculated from components")
            rows.append({
                "item": item, "year": yr, "has_own_report_value": not own.empty,
                "value_used": val, "unit": used["unit"] if used is not None else "",
                "value_source": src, "quality": used["quality"] if used is not None else "",
                "yoy_change_pct": round(yoy, 1) if pd.notna(yoy) else np.nan,
                "flag_yoy_gt30": bool(pd.notna(yoy) and abs(yoy) > YOY_FLAG),
                "note": "; ".join(notes),
            })
            prev = val  # change is calculated against the previous year only
    return pd.DataFrame(rows)


def main():
    cand = pd.read_csv(CAND_IN)
    d = prepare(cand)
    d = add_derived_totals(d)
    table = select(d)
    TABLE_OUT.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(TABLE_OUT, index=False, encoding="utf-8")

    checks = build_checks(table)
    CHECK_OUT.parent.mkdir(parents=True, exist_ok=True)
    checks.to_csv(CHECK_OUT, index=False, encoding="utf-8")

    print(f"Table: {TABLE_OUT.relative_to(ROOT)} ({len(table)} rows, {table['item'].nunique()} items)")
    print(f"Check: {CHECK_OUT.relative_to(ROOT)} (indicator-years without a primary value: "
          f"{(~checks['has_own_report_value']).sum()}, changes above {YOY_FLAG:g}%: {checks['flag_yoy_gt30'].sum()})")


if __name__ == "__main__":
    main()
