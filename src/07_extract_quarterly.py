"""
07 – Nominal quarterly series (2016 Q1 – 2023 Q3).

Source : Data/FinancialStatements/YYYY/Cimsa_YYYY_MM.pdf
         2016–2022: 03, 06, 09, 12; 2023: 03, 06, 09 (2023_12 is IAS 29 and is not part of the nominal series)
Items  : revenue, cost_of_sales (income statement) + the five items of the cost note and
         total_production_cost. Only current-period (own report) columns are read.
Outputs:
  output/tables/quarterly_cumulative.csv  (cumulative values: Q1, H1, 9M, FY; also the reports'
                                           "last 3 months" columns, column_type = last_3_months)
  output/tables/quarterly_series.csv      (derived Q1–Q4 + gross_margin, energy_share_production_cost,
                                           energy_to_revenue)
  output/checks/quarterly_checks.csv      (a) FY = cost_items_annual, (b) derived = last-3-months column,
                                           (c) negative quarter, (d) sum of the five items = total production cost
Columns are identified from the "1 Ocak / 1 Nisan / 1 Temmuz" (1 January / April / July) and year phrases in the header.
The unit is read from each report's "(Tutarlar ... TL / bin TL ...)" phrase; everything is converted to TRY.
"""
import importlib.util
from pathlib import Path
import re

import numpy as np
import pandas as pd
import pdfplumber

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "Data" / "FinancialStatements"
CUM_OUT = ROOT / "output" / "tables" / "quarterly_cumulative.csv"
SER_OUT = ROOT / "output" / "tables" / "quarterly_series.csv"
CHECK_OUT = ROOT / "output" / "checks" / "quarterly_checks.csv"
COST_ITEMS = ROOT / "output" / "tables" / "cost_items_annual.csv"

# src/01's labels and text helpers
_spec = importlib.util.spec_from_file_location("ci01", ROOT / "src" / "01_extract_cost_items.py")
ci01 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ci01)
normalize, PL_ITEMS, NOTE_ITEMS, FIVE_ITEMS = ci01.normalize, ci01.PL_ITEMS, ci01.NOTE_ITEMS, ci01.FIVE_ITEMS

REPORTS = [(y, m) for y in range(2016, 2024) for m in ("03", "06", "09", "12") if not (y == 2023 and m == "12")]
PERIOD_OF = {"03": "Q1", "06": "H1", "09": "9M", "12": "FY"}
QUARTER_OF = {"06": "Q2", "09": "Q3"}           # quarter of the last-3-months column
START_OF = {"03": "ocak", "06": "nisan", "09": "temmuz"}
MONTH_EN = {"ocak": "January", "nisan": "April", "temmuz": "July"}  # for notes only
NUM_RE = re.compile(r"\(?\d{1,3}(?:\.\d{3})+\)?")
ITEMS = ["revenue", "cost_of_sales"] + FIVE_ITEMS + ["total_production_cost"]
PREF_TOL = 1000  # TRY; consistency tolerance for the last-3-months column


# ---------------------------------------------------------------- reading helpers

def numbers(line: str) -> list[float]:
    return [float(t.strip("()").replace(".", "")) for t in NUM_RE.findall(line)]


def value_tokens(line: str):
    """Number cells after the label, keeping position: malformed cell None, note numbers skipped."""
    toks = line.split()
    first = next((k for k, t in enumerate(toks) if NUM_RE.fullmatch(t) or ("." in t and any(ch.isdigit() for ch in t))), None)
    if first is None:
        return []
    out = []
    for t in toks[first:]:
        if not any(ch.isdigit() for ch in t):
            continue  # stray "(" etc.
        out.append(float(t.strip("()").replace(".", "")) if NUM_RE.fullmatch(t) else None)
    return out


def is_subsequence(short: str, long: str) -> bool:
    it = iter(long)
    return all(ch in it for ch in short)


def detect_factor(pages) -> tuple[int, str]:
    head = normalize(" ".join(" ".join(p) for p in pages[:8]))
    if "binturklirasi" in head or "bintl" in head:
        return 1000, "report amounts in TRY thousand"
    return 1, "report amounts in TRY"


def column_map(lines, i, report_year):
    """Identifies the columns for the table's first data line i: (current cumulative index, current last-3-months index, column count)."""
    starts, years = None, None
    for j in range(i - 1, max(i - 10, -1), -1):
        n = normalize(lines[j])
        if starts is None:
            s = re.findall(r"1(ocak|nisan|tem[a-z]*?(?=-|–|30|$|1)|ekim)", n)
            if len(s) >= 2:
                starts = ["temmuz" if x.startswith("tem") else x for x in s]
        if years is None:
            ys = re.findall(r"(20\d\d)", n)
            if len(ys) >= 2 and len(set(ys)) <= 2:
                years = [int(y) for y in ys]
        if starts and years:
            break
    if years is None:
        return None, None, None, "years not found in the column header"
    if starts is None:
        starts = ["ocak"] * len(years)  # 3-month report: all columns start on 1 January
    if len(starts) != len(years):
        return None, None, None, f"header mismatch (starts {starts}, years {years})"
    cols = list(zip(starts, years))
    cum = next((k for k, c in enumerate(cols) if c == ("ocak", report_year)), None)
    last3 = next((k for k, c in enumerate(cols) if c[0] != "ocak" and c[1] == report_year), None)
    return cum, last3, len(cols), ""


def find_rows(lines, items, start=0, end=None):
    """{item: line index} – first matching line for each item (with at least two numbers)."""
    out = {}
    for i in range(start, len(lines) if end is None else end):
        n = normalize(lines[i])
        for item, prefixes in items.items():
            if item not in out and any(n.startswith(p) for p in prefixes):
                if len(numbers(lines[i])) >= 2:
                    out[item] = i
                elif i + 1 < len(lines) and len(numbers(lines[i + 1])) >= 2 and not re.search(r"[a-zA-ZçğıöşüÇĞİÖŞÜ]", lines[i + 1]):
                    out[item] = i + 1  # numbers on the line below the label
    return out


def locate(pages):
    """Finds the income statement and cost note lines: [(item, page, lines, line index)]."""
    found = []
    for pno, lines in enumerate(pages[:20], start=1):
        rows = find_rows(lines, PL_ITEMS)
        if len(rows) == 2:
            found += [(it, pno, lines, idx) for it, idx in rows.items()]
            break
    for pno, lines in enumerate(pages, start=1):
        norms = [normalize(l) for l in lines]
        end = next((k for k, n in enumerate(norms) if n.startswith("toplamuretimmaliyeti")), None)
        if end is None:
            continue
        rows = find_rows(lines, NOTE_ITEMS, max(0, end - 12), end + 1)
        if "total_production_cost" in rows:
            fuzzy = {}
            missing = [it for it in NOTE_ITEMS if it not in rows]
            used = set(rows.values())
            for k in range(max(0, end - 8), end):
                if k in used or len(numbers(lines[k])) < 2:
                    continue
                lab = re.sub(r"[\d().\-]", "", norms[k])
                hits = [it for it in missing if any(is_subsequence(p, lab) for p in NOTE_ITEMS[it])]
                if len(hits) == 1 and hits[0] not in fuzzy:
                    fuzzy[hits[0]] = k
            found += [(it, pno, lines, idx) for it, idx in rows.items()]
            found += [(it, pno, lines, idx, "label read garbled, identified by letter-order matching (raw: "
                       f"'{lines[idx].split('(')[0].strip()}')") for it, idx in fuzzy.items()]
            break
    return found


# ---------------------------------------------------------------- main extraction

def extract(year, month):
    path = DATA_DIR / str(year) / f"Cimsa_{year}_{month}.pdf"
    rel = path.relative_to(ROOT).as_posix()
    with pdfplumber.open(path) as pdf:
        pages = [(p.extract_text() or "").split("\n") for p in pdf.pages]
    factor, unit_note = detect_factor(pages)
    full_text = "\n".join("\n".join(p) for p in pages)
    rows, seen = [], set()
    for item, pno, lines, idx, *extra in locate(pages):
        seen.add(item)
        cum, last3, ncol, why = column_map(lines, idx, year)
        vals = value_tokens(lines[idx])
        note = [unit_note] + list(extra)
        # Split number: "Hasılat 16 24 8.492.513" -> 248.492.513; merged only if the combined number appears
        # verbatim elsewhere in the report (no merging without evidence)
        sm = re.match(r"^\D+?\s\d{1,2}\s(\d{1,3})\s(\d{1,3}(?:\.\d{3})+)(?=\s|$)", lines[idx].strip())
        if sm:
            merged = sm.group(1) + sm.group(2)
            if NUM_RE.fullmatch(merged) and re.search(rf"(?<![\d.]){re.escape(merged)}(?![\d.])", full_text):
                vals[0] = float(merged.replace(".", ""))
                note.append(f"number split in the PDF text ('{sm.group(1)} {sm.group(2)}'); combined value "
                            f"{merged} used because it appears verbatim elsewhere in the report")
        if why:
            note.append(why)
        if ncol is not None and len(vals) > ncol:
            note.append(f"line has {len(vals)} number cells; first {ncol} used (raw: '{lines[idx].strip()}')")
            vals = vals[:ncol]
        ok = ncol is not None and len(vals) == ncol
        if ncol is not None and not ok:
            note.append(f"number count ({len(vals)}) does not match the column count ({ncol}); raw line: '{lines[idx].strip()}'")
        base = {"year": year, "item": item, "unit": "TRY", "basis": "nominal", "restated": False,
                "source_file": rel, "source_page": pno}
        v = vals[cum] * factor if ok and cum is not None and vals[cum] is not None else None
        rows.append({**base, "period": PERIOD_OF[month], "column_type": "cumulative", "value": v,
                     "note": "; ".join(note + ([] if v is not None else ["value unreadable"]))})
        if month in QUARTER_OF and ok and last3 is not None and vals[last3] is not None:
            rows.append({**base, "period": QUARTER_OF[month], "column_type": "last_3_months",
                         "value": vals[last3] * factor,
                         "note": "; ".join(note + [f"the report's '1 {MONTH_EN[START_OF[month]]} – period end' (last 3 months) column"])})
    for item in ITEMS:
        if item not in seen:
            rows.append({"year": year, "item": item, "unit": "TRY", "basis": "nominal", "restated": False,
                         "source_file": rel, "source_page": None, "period": PERIOD_OF[month],
                         "column_type": "cumulative", "value": None, "note": "not found in report"})
    return rows


def derive_quarters(cum: pd.DataFrame) -> pd.DataFrame:
    c = cum[cum["column_type"] == "cumulative"]
    get = {(r["year"], r["item"], r["period"]): r for _, r in c.iterrows()}
    out = []
    plan = [("Q1", "Q1", None), ("Q2", "H1", "Q1"), ("Q3", "9M", "H1"), ("Q4", "FY", "9M")]
    for year in sorted(c["year"].unique()):
        for item in ITEMS:
            for q, cur, prev in plan:
                if year == 2023 and q == "Q4":
                    continue
                a = get.get((year, item, cur))
                b = get.get((year, item, prev)) if prev else None
                if a is None:
                    continue
                if prev is None:
                    v, formula, src = a["value"], "Q1 = 3-month report", a["source_file"]
                else:
                    v = a["value"] - b["value"] if b is not None and pd.notna(a["value"]) and pd.notna(b["value"]) else None
                    formula = f"{q} = {cur} − {prev}"
                    src = f"{a['source_file']} − {b['source_file'] if b is not None else '?'}"
                note = [formula]
                if v is None:
                    note.append("input missing; not computed")
                out.append({"year": year, "period": q, "item": item, "value": v, "unit": "TRY", "basis": "nominal",
                            "restated": False, "source_file": src, "source_page": None, "note": "; ".join(note)})
    s = pd.DataFrame(out)

    # Preferred value: in Q2–Q3 the report's "last 3 months" column, if in that column the sum of the five items =
    # total_production_cost (tolerance TRY 1,000); otherwise the derived value. Q1 and Q4: derived.
    l3 = cum[cum["column_type"] == "last_3_months"]
    valid = {}
    for (y, q), g in l3.groupby(["year", "period"]):
        v = g.set_index("item")["value"]
        five, tot = v.reindex(FIVE_ITEMS), v.get("total_production_cost")
        valid[(y, q)] = bool(five.notna().all() and pd.notna(tot) and abs(five.sum() - tot) <= PREF_TOL)
    l3v = {(r["year"], r["period"], r["item"]): r["value"] for _, r in l3.iterrows()}
    pref, src = [], []
    for _, r in s.iterrows():
        key3 = (r["year"], r["period"], r["item"])
        if valid.get((r["year"], r["period"])) and pd.notna(l3v.get(key3)):
            pref.append(l3v[key3])
            src.append("last_3_months")
        else:
            pref.append(r["value"])
            src.append("derived")
    s["value_preferred"], s["preferred_source"] = pref, src

    # Ratios: separately with the derived values (value) and the preferred values (value_preferred)
    ratios = {
        "gross_margin": ("(revenue − cost_of_sales) / revenue", ["revenue", "cost_of_sales"],
                         lambda r: (r["revenue"] - r["cost_of_sales"]) / r["revenue"]),
        "energy_share_production_cost": ("energy_cost / total_production_cost", ["energy_cost", "total_production_cost"],
                                         lambda r: r["energy_cost"] / r["total_production_cost"]),
        "energy_to_revenue": ("energy_cost / revenue", ["energy_cost", "revenue"],
                              lambda r: r["energy_cost"] / r["revenue"]),
    }

    def safe(fn, r):
        try:
            v = fn(r)
        except (KeyError, ZeroDivisionError):
            return None
        return v if pd.notna(v) and np.isfinite(v) else None

    w = s.pivot_table(index=["year", "period"], columns="item", values="value", aggfunc="first")
    wp = s.pivot_table(index=["year", "period"], columns="item", values="value_preferred", aggfunc="first")
    srcs = s.set_index(["year", "period", "item"])["preferred_source"]
    rr = []
    for (year, q), r in w.iterrows():
        for name, (formula, inputs, fn) in ratios.items():
            v, vp = safe(fn, r), safe(fn, wp.loc[(year, q)])
            from3 = any(srcs.get((year, q, i)) == "last_3_months" for i in inputs)
            rr.append({"year": year, "period": q, "item": name, "value": v, "unit": "ratio", "basis": "nominal",
                       "restated": False, "source_file": "derived", "source_page": None,
                       "note": formula + ("" if v is not None else "; input missing"),
                       "value_preferred": vp, "preferred_source": "last_3_months" if from3 else "derived"})
    s = pd.concat([s, pd.DataFrame(rr)], ignore_index=True)
    s["_q"] = s["period"].str[1].astype(int)
    return s.sort_values(["item", "year", "_q"]).drop(columns="_q").reset_index(drop=True)


def build_checks(cum: pd.DataFrame, ser: pd.DataFrame) -> pd.DataFrame:
    out = []
    # a) FY = cost_items_annual own-report values
    ci = pd.read_csv(COST_ITEMS)
    ci = ci[(~ci["restated"]) & (ci["basis"] == "nominal")]
    fy = cum[(cum["period"] == "FY") & (cum["column_type"] == "cumulative")]
    m = fy.merge(ci[["year", "item", "value"]], on=["year", "item"], how="left", suffixes=("", "_annual"))
    for _, r in m.iterrows():
        ok = pd.notna(r["value"]) and pd.notna(r["value_annual"])
        out.append({"check": "a_fy_vs_cost_items_annual", "year": r["year"], "period": "FY", "item": r["item"],
                    "lhs": r["value"], "rhs": r["value_annual"], "difference": r["value"] - r["value_annual"] if ok else None,
                    "passed": bool(ok and r["value"] == r["value_annual"]), "note": ""})
    # b) derived quarter = the report's last-3-months column
    l3 = cum[cum["column_type"] == "last_3_months"]
    m = l3.merge(ser[["year", "period", "item", "value"]], on=["year", "period", "item"], how="left",
                 suffixes=("_reported", "_derived"))
    for _, r in m.iterrows():
        ok = pd.notna(r["value_reported"]) and pd.notna(r["value_derived"])
        diff = r["value_derived"] - r["value_reported"] if ok else None
        tol = 2 * (1000 if "TRY thousand" in r["note"] else 1) + 0.5  # difference of two rounded cumulative values
        out.append({"check": "b_derived_vs_reported_last_3_months", "year": r["year"], "period": r["period"],
                    "item": r["item"], "lhs": r["value_derived"], "rhs": r["value_reported"], "difference": diff,
                    "passed": bool(ok and abs(diff) <= tol),
                    "note": f"report: {r['source_file']}" + ("" if not ok or abs(diff) <= tol else
                            "; difference: the report's last-3-months column may be wrong, or the prior quarter's value may have been "
                            "reclassified in this report (see check d)")})
    # c) negative quarter values (amount items)
    neg = ser[(ser["unit"] == "TRY") & (ser["value"] < 0)]
    for _, r in neg.iterrows():
        out.append({"check": "c_negative_quarter", "year": r["year"], "period": r["period"], "item": r["item"],
                    "lhs": r["value"], "rhs": None, "difference": None, "passed": False,
                    "note": f"{r['note']}; source: {r['source_file']}"})
    if neg.empty:
        out.append({"check": "c_negative_quarter", "year": None, "period": None, "item": "all", "lhs": None,
                    "rhs": None, "difference": None, "passed": True, "note": "no negative quarter values"})
    # d) sum of the five items = total production cost
    for (y, p, src, ctype), g in cum.groupby(["year", "period", "source_file", "column_type"]):
        v = g.set_index("item")["value"]
        five = v.reindex(FIVE_ITEMS)
        tot = v.get("total_production_cost")
        ok = five.notna().all() and pd.notna(tot)
        s = five.sum() if ok else None
        tol = 5 * (1000 if "TRY thousand" in str(g["note"].iloc[0]) else 1) + 0.5
        out.append({"check": "d_five_items_vs_total_production_cost", "year": y, "period": p,
                    "item": f"five_items ({ctype})", "lhs": s, "rhs": tot, "difference": (s - tot) if ok else None,
                    "passed": bool(ok and abs(s - tot) <= tol), "note": src if ok else f"{src}; missing item"})
    # e) sum of the preferred quarters vs the annual value
    fyv = {(r["year"], r["item"]): r["value"] for _, r in fy.iterrows()}
    amt = ser[ser["unit"] == "TRY"]
    for (y, item), g in amt.groupby(["year", "item"]):
        if (y, item) not in fyv or len(g) < 4:
            continue  # 2023: no annual nominal value
        tot = g["value_preferred"].sum(min_count=4)
        annual = fyv[(y, item)]
        ok = pd.notna(tot) and pd.notna(annual)
        n3 = int((g["preferred_source"] == "last_3_months").sum())
        out.append({"check": "e_preferred_quarters_sum_vs_annual", "year": y, "period": "FY", "item": item,
                    "lhs": tot, "rhs": annual, "difference": (tot - annual) if ok else None,
                    "passed": bool(ok and abs(tot - annual) <= PREF_TOL),
                    "note": f"quarters taken from the last-3-months column: {n3}; the difference arises because the quarters come from "
                            "different reports with different classifications (within-year reclassification)" if ok and abs(tot - annual) > PREF_TOL else
                            f"quarters taken from the last-3-months column: {n3}"})
    res = pd.DataFrame(out)
    return res


def main():
    rows = []
    for y, mth in REPORTS:
        r = extract(y, mth)
        miss = [x["item"] for x in r if x["column_type"] == "cumulative" and x["value"] is None]
        n3 = sum(1 for x in r if x["column_type"] == "last_3_months")
        print(f"{y}_{mth}: {len(r)} rows (last 3 months: {n3})" + (f" | EMPTY: {miss}" if miss else ""))
        rows.extend(r)
    cum = pd.DataFrame(rows)
    order = {"Q1": 0, "H1": 1, "9M": 2, "FY": 3, "Q2": 1, "Q3": 2}
    cum["_o"] = cum["period"].map(order)
    cum = cum.sort_values(["item", "year", "_o", "column_type"]).drop(columns="_o").reset_index(drop=True)
    cum["source_page"] = cum["source_page"].astype("Int64")
    cols = ["year", "period", "item", "value", "unit", "basis", "restated", "source_file", "source_page",
            "column_type", "note"]
    cum = cum[cols]
    CUM_OUT.parent.mkdir(parents=True, exist_ok=True)
    cum.to_csv(CUM_OUT, index=False, encoding="utf-8")

    ser = derive_quarters(cum)
    ser.to_csv(SER_OUT, index=False, encoding="utf-8")

    checks = build_checks(cum, ser)
    CHECK_OUT.parent.mkdir(parents=True, exist_ok=True)
    checks.to_csv(CHECK_OUT, index=False, encoding="utf-8")
    print(f"\nCumulative: {CUM_OUT.relative_to(ROOT)} ({len(cum)} rows)")
    print(f"Quarterly : {SER_OUT.relative_to(ROOT)} ({len(ser)} rows)")
    for c, g in checks.groupby("check"):
        print(f"Check {c}: {int(g['passed'].sum())}/{len(g)} passed")


if __name__ == "__main__":
    main()
