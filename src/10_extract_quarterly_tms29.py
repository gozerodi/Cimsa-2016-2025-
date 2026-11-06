"""
10 – Quarterly ratios for the IAS 29 period (2023 Q4, 2024 Q1–Q4, 2025 Q1–Q4).

Source : Data/FinancialStatements/YYYY/Cimsa_YYYY_MM.pdf – 2023: 09 (nominal) and 12 (IAS 29); 2024 and 2025: 03, 06, 09, 12.
         Reading uses extract() from src/07_extract_quarterly.py (same labels, unit detection, column map).
Items  : revenue, cost_of_sales, energy_cost, total_production_cost (current-period cumulative + "last 3 months" where available).
Ratios : gross_margin = (revenue − cost_of_sales) / revenue; energy_share_production_cost = energy_cost /
         total_production_cost; energy_to_revenue = energy_cost / revenue.

Quarter derivation:
  Q1     = 3-month report (March purchasing power).
  Q2, Q3 = the report's "last 3 months" column (June / September purchasing power). Otherwise: cumulative − previous cumulative × CPI ratio.
  Q4     = 12-month − 9-month × (CPI December / CPI September). Because IAS 29 reports are stated in the purchasing power of
           different dates, the 9-month amount is first rolled forward to December purchasing power (approximate method).
  2023 Q4 = 2023 annual (IAS 29) − 2023 9-month (NOMINAL) × (CPI December 2023 / September 2023): mixed nominal–IAS 29, approximate.
The analysis uses the RATIOS; amounts are for traceability only (each quarter in its own purchasing power).

Outputs:
  output/tables/quarterly_ratios_tms29.csv   (long format + purchasing_power, use columns)
  output/checks/quarterly_tms29_checks.csv   (sum of 4 quarters indexed to December purchasing power ≈ annual value)
"""
import importlib.util
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TABLE_OUT = ROOT / "output" / "tables" / "quarterly_ratios_tms29.csv"
CHECK_OUT = ROOT / "output" / "checks" / "quarterly_tms29_checks.csv"
CPI_IN = ROOT / "output" / "tables" / "cpi_monthly.csv"

_spec = importlib.util.spec_from_file_location("q07", ROOT / "src" / "07_extract_quarterly.py")
q07 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(q07)

ITEMS = ["revenue", "cost_of_sales", "energy_cost", "total_production_cost"]
REPORTS = {2023: ["09", "12"], 2024: ["03", "06", "09", "12"], 2025: ["03", "06", "09", "12"]}
MONTH_OF = {"Q1": 3, "Q2": 6, "Q3": 9, "Q4": 12}
RATIOS = {
    "gross_margin": lambda d: (d["revenue"] - d["cost_of_sales"]) / d["revenue"],
    "energy_share_production_cost": lambda d: d["energy_cost"] / d["total_production_cost"],
    "energy_to_revenue": lambda d: d["energy_cost"] / d["revenue"],
}


def cpi_lookup():
    cpi = pd.read_csv(CPI_IN)
    return {(int(r["year"]), int(r["period"][1:])): r["value"] for _, r in cpi.iterrows()}


def read_reports():
    rows = []
    for y, months in REPORTS.items():
        for m in months:
            for r in q07.extract(y, m):
                if r["item"] in ITEMS:
                    r = dict(r)
                    r["basis"] = "nominal" if (y == 2023 and m != "12") else "tms29"
                    r["month"] = int(m)
                    rows.append(r)
    return pd.DataFrame(rows)


def get(cum, year, month, item, column_type):
    r = cum[(cum["year"] == year) & (cum["month"] == month) & (cum["item"] == item) & (cum["column_type"] == column_type)]
    return (r["value"].iloc[0], r["source_file"].iloc[0], r["source_page"].iloc[0]) if len(r) and pd.notna(r["value"].iloc[0]) \
        else (None, None, None)


def derive(cum, cpi):
    out = []
    for y in REPORTS:
        quarters = ["Q4"] if y == 2023 else ["Q1", "Q2", "Q3", "Q4"]
        for q in quarters:
            m = MONTH_OF[q]
            for item in ITEMS:
                note, src, page = [], None, None
                if q == "Q1":
                    v, src, page = get(cum, y, 3, item, "cumulative")
                    note.append("Q1 = 3-month report")
                elif q in ("Q2", "Q3"):
                    v, src, page = get(cum, y, m, item, "last_3_months")
                    if v is not None:
                        note.append("the report's 'last 3 months' column")
                    else:
                        cur, src, page = get(cum, y, m, item, "cumulative")
                        prev, psrc, _ = get(cum, y, m - 3, item, "cumulative")
                        f = cpi[(y, m)] / cpi[(y, m - 3)]
                        v = cur - prev * f if cur is not None and prev is not None else None
                        src = f"{src} − {psrc}" if src else None
                        note.append(f"no last-3-months column; cumulative difference, previous cumulative rolled to the same purchasing "
                                    f"power with the CPI ({m}/{m-3}) ratio {f:.4f} (approximate)")
                else:  # Q4
                    fy, fsrc, page = get(cum, y, 12, item, "cumulative")
                    nm, nsrc, _ = get(cum, y, 9, item, "cumulative")
                    f = cpi[(y, 12)] / cpi[(y, 9)]
                    v = fy - nm * f if fy is not None and nm is not None else None
                    src = f"{fsrc} − {nsrc}" if fsrc else None
                    note.append(f"Q4 = 12-month − 9-month × CPI (December/September {y}) = {f:.4f}; approximate method "
                                "(9-month amount indexed to December purchasing power)")
                    if y == 2023:
                        note.append("mixed nominal–IAS 29, approximate: 2023 annual is IAS 29, 9-month is nominal (because nominal amounts "
                                    "are in within-period prices, indexing may under-carry the 9-month part)")
                if v is None:
                    note.append("value not computed")
                out.append({"year": y, "period": q, "item": item, "value": v, "unit": "TRY", "basis": "tms29",
                            "restated": False, "source_file": src, "source_page": page,
                            "note": "; ".join(note + ["amount for traceability only; in the quarter's own purchasing power"]),
                            "purchasing_power": f"{y}-{m:02d}", "use": "traceability"})
    amt = pd.DataFrame(out)
    # Ratios
    ratio_rows = []
    for (y, q), g in amt.groupby(["year", "period"], sort=False):
        d = dict(zip(g["item"], g["value"]))
        base = g.iloc[0]
        q_note = "; ".join(n for n in base["note"].split("; ") if not n.startswith("amount for traceability only"))
        for name, fn in RATIOS.items():
            try:
                v = fn(d) if all(d.get(k) is not None for k in ITEMS) else None
            except ZeroDivisionError:
                v = None
            ratio_rows.append({"year": y, "period": q, "item": name, "value": v, "unit": "ratio", "basis": "tms29",
                               "restated": False, "source_file": base["source_file"], "source_page": None,
                               "note": "ratio; from the same quarter's amounts; " + q_note,
                               "purchasing_power": "ratio (independent of purchasing power)", "use": "analysis"})
    return pd.concat([pd.DataFrame(ratio_rows), amt], ignore_index=True)


def build_checks(cum, ser, cpi):
    rows = []
    for y in REPORTS:
        for item in ITEMS:
            fy, _, _ = get(cum, y, 12, item, "cumulative")
            if y == 2023:
                # Q1–Q3 are in the nominal series; here the 9-month last-3-months column exists only for Q3
                rows.append({"check": "indexed_quarters_sum_vs_fy", "year": y, "item": item, "status": "not checked",
                             "indexed_sum": None, "fy_value": fy, "diff": None, "diff_pct": None,
                             "detail": "2023 Q1–Q3 are in the nominal quarterly series (quarterly_series.csv); Q4 is by definition FY − indexed 9 months"})
                continue
            s = ser[(ser["year"] == y) & (ser["item"] == item)].set_index("period")["value"]
            parts = {q: s.get(q) for q in ["Q1", "Q2", "Q3", "Q4"]}
            if any(v is None or pd.isna(v) for v in parts.values()) or fy is None:
                rows.append({"check": "indexed_quarters_sum_vs_fy", "year": y, "item": item, "status": "warning",
                             "indexed_sum": None, "fy_value": fy, "diff": None, "diff_pct": None, "detail": "missing quarter"})
                continue
            idx = sum(parts[q] * cpi[(y, 12)] / cpi[(y, MONTH_OF[q])] for q in ["Q1", "Q2", "Q3"]) + parts["Q4"]
            d = idx - fy
            pct = d / fy * 100
            rows.append({"check": "indexed_quarters_sum_vs_fy", "year": y, "item": item,
                         "status": "passed" if abs(pct) <= 2 else "warning", "indexed_sum": idx, "fy_value": fy,
                         "diff": d, "diff_pct": round(pct, 3),
                         "detail": "Q1–Q3 rolled to December purchasing power with CPI (December/quarter-end month) + Q4; "
                                   "tolerance 2%. The difference shows how consistent the last-3-months columns are with the 9-month cumulative "
                                   "(because Q4 = FY − indexed 9M)"})
            # extra: 9-month cumulative vs indexed Q1–Q3 (September purchasing power)
            nm, _, _ = get(cum, y, 9, item, "cumulative")
            idx9 = sum(parts[q] * cpi[(y, 9)] / cpi[(y, MONTH_OF[q])] for q in ["Q1", "Q2", "Q3"])
            rows.append({"check": "indexed_q1_q3_vs_9m", "year": y, "item": item,
                         "status": "passed" if abs(idx9 / nm - 1) <= 0.02 else "warning", "indexed_sum": idx9, "fy_value": nm,
                         "diff": idx9 - nm, "diff_pct": round((idx9 / nm - 1) * 100, 3),
                         "detail": "Q1–Q3 indexed to September purchasing power vs the 9-month cumulative (9M in the fy_value column)"})
    # ratio range check
    rat = ser[ser["use"] == "analysis"]
    for _, r in rat.iterrows():
        bad = r["value"] is None or pd.isna(r["value"]) or not (-0.5 < r["value"] < 1.0)
        if bad:
            rows.append({"check": "ratio_range", "year": r["year"], "item": f"{r['item']} {r['period']}", "status": "warning",
                         "indexed_sum": None, "fy_value": None, "diff": None, "diff_pct": None,
                         "detail": f"ratio outside the expected range or empty: {r['value']}"})
    return pd.DataFrame(rows)


def main():
    cpi = cpi_lookup()
    cum = read_reports()
    ser = derive(cum, cpi)
    checks = build_checks(cum, ser, cpi)
    cols = ["year", "period", "item", "value", "unit", "basis", "restated", "source_file", "source_page", "note",
            "purchasing_power", "use"]
    ser = ser[cols].sort_values(["use", "year", "period", "item"]).reset_index(drop=True)
    TABLE_OUT.parent.mkdir(parents=True, exist_ok=True)
    ser.to_csv(TABLE_OUT, index=False, encoding="utf-8")
    CHECK_OUT.parent.mkdir(parents=True, exist_ok=True)
    checks.to_csv(CHECK_OUT, index=False, encoding="utf-8")
    piv = ser[ser["use"] == "analysis"].pivot_table(index=["year", "period"], columns="item", values="value")
    print((piv * 100).round(1).to_string())
    print(f"Table: {TABLE_OUT.relative_to(ROOT)} ({len(ser)} rows)")
    print(f"Check: {CHECK_OUT.relative_to(ROOT)} ({len(checks)} rows)")
    print(checks["status"].value_counts().to_string())


if __name__ == "__main__":
    main()
