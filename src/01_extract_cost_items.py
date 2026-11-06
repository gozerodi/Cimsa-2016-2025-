"""
01 – Extracts cost items from the annual (12-month) financial reports.

Source : Data/FinancialStatements/YYYY/Cimsa_YYYY_12.pdf (2016–2025)
Outputs:
  output/tables/cost_items_annual.csv         (long format)
  output/checks/cost_items_annual_checks.csv  (sum of the five items = total production cost?)

Two columns are read from each report: the current year (restated=False) and the comparative year
(restated=True, year = prior year). All values are stored as positive amounts in TRY.
"""
from pathlib import Path
import re

import pandas as pd
import pdfplumber

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "Data" / "FinancialStatements"
TABLE_OUT = ROOT / "output" / "tables" / "cost_items_annual.csv"
CHECK_OUT = ROOT / "output" / "checks" / "cost_items_annual_checks.csv"

YEARS = range(2016, 2026)
PL_SEARCH_PAGES = 20  # the income statement is in the first pages of the report

# Item -> normalized line prefix (no spaces, lower case, ASCII)
PL_ITEMS = {
    "revenue": ["hasilat"],
    "cost_of_sales": ["satislarinmaliyeti"],
}
NOTE_ITEMS = {
    "raw_material": ["direkthammaddevemalzeme"],
    "direct_labor": ["direktiscilik", "iscilikgiderleri"],
    "energy_cost": ["enerjigiderleri"],
    "depreciation": ["amortismanveitfagiderpayi"],
    "other_production": ["digeruretimgiderleri"],
    "total_production_cost": ["toplamuretimmaliyeti"],
}
FIVE_ITEMS = ["raw_material", "direct_labor", "energy_cost", "depreciation", "other_production"]

# Number with thousands separators (e.g. 1.395.817.135 or (918.454))
NUM_RE = re.compile(r"\(?\d{1,3}(?:\.\d{3})+\)?")

# Broken font encoding in the 2016 reports (Ģ = ş, ġ = Ş, Ġ = İ)
BROKEN_CHARS = str.maketrans({"Ģ": "ş", "ġ": "Ş", "Ġ": "İ"})
ASCII_MAP = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")


def normalize(line: str) -> str:
    """For label matching: fix the encoding, remove spaces, lower case, ASCII."""
    line = line.translate(BROKEN_CHARS).translate(ASCII_MAP).lower()
    return re.sub(r"\s+", "", line)


def parse_numbers(line: str) -> list[int]:
    """Returns the numbers with thousands separators in a line as positive integers."""
    return [int(tok.strip("()").replace(".", "")) for tok in NUM_RE.findall(line)]


def find_item(lines: list[str], prefixes: list[str]):
    """Finds the first matching line; returns (current, comparative, line text, matched prefix)."""
    for line in lines:
        norm = normalize(line)
        for prefix in prefixes:
            if norm.startswith(prefix):
                nums = parse_numbers(line)
                if len(nums) >= 2:
                    return nums[0], nums[1], line, prefix
    return None


def find_pl_page(pdf):
    """Income statement: the first page with both revenue and cost of sales lines carrying numbers."""
    for i, page in enumerate(pdf.pages[:PL_SEARCH_PAGES]):
        lines = (page.extract_text() or "").split("\n")
        if find_item(lines, PL_ITEMS["revenue"]) and find_item(lines, PL_ITEMS["cost_of_sales"]):
            return i + 1, lines
    return None, []


def find_note_section(pdf):
    """Cost of sales section of the 'expenses by nature' note (up to the total line)."""
    for i, page in enumerate(pdf.pages):
        lines = (page.extract_text() or "").split("\n")
        norms = [normalize(l) for l in lines]
        start = next((k for k, n in enumerate(norms) if "satilanmalinmaliyeti" in n), None)
        if start is None:
            continue
        end = next((k for k, n in enumerate(norms[start:], start) if n.startswith("toplamuretimmaliyeti")), None)
        if end is not None:
            return i + 1, lines[start:end + 1]
    return None, []


def extract_report(year: int):
    path = DATA_DIR / str(year) / f"Cimsa_{year}_12.pdf"
    rel = path.relative_to(ROOT).as_posix()
    basis = "nominal" if year <= 2022 else "tms29"
    factor = 1 if year <= 2022 else 1000  # 2023+ reports are in TRY thousand

    with pdfplumber.open(path) as pdf:
        pl_page, pl_lines = find_pl_page(pdf)
        note_page, note_lines = find_note_section(pdf)

    rows, missing = [], []
    # Lines with numbers in the note other than the five items + total (for the check note)
    known = [pre for pres in NOTE_ITEMS.values() for pre in pres]
    extra_lines = [l.strip() for l in note_lines
                   if parse_numbers(l) and not any(normalize(l).startswith(k) for k in known)]
    sources = [(PL_ITEMS, pl_page, pl_lines), (NOTE_ITEMS, note_page, note_lines)]
    for items, page_no, lines in sources:
        for item, prefixes in items.items():
            hit = find_item(lines, prefixes)
            notes = []
            if hit is None:
                missing.append(item)
                cur = prev = None
            else:
                cur, prev, raw_line, prefix = hit
                cur, prev = cur * factor, prev * factor
                if item == "direct_labor" and prefix == "iscilikgiderleri":
                    notes.append("item named 'İşçilik giderleri' in the report (no 'direct' wording)")
                if len(parse_numbers(raw_line)) > 2:
                    notes.append(f"more than 2 numbers read in the line, first two used: '{raw_line.strip()}'")
            for col_year, value, restated in [(year, cur, False), (year - 1, prev, True)]:
                note = list(notes)
                if restated:
                    note.append(f"comparative column of the {year} report")
                if value is None:
                    note.append("not found in report")
                rows.append({
                    "year": col_year,
                    "period": "FY",
                    "item": item,
                    "value": value,
                    "unit": "TRY",
                    "basis": basis,
                    "restated": restated,
                    "source_file": rel,
                    "source_page": page_no,
                    "note": "; ".join(note),
                })
    return rows, missing, extra_lines


def build_checks(df: pd.DataFrame, extras: dict) -> pd.DataFrame:
    checks = []
    for (src, year, restated), g in df.groupby(["source_file", "year", "restated"]):
        vals = g.set_index("item")["value"]
        five = vals.reindex(FIVE_ITEMS)
        total = vals.get("total_production_cost")
        complete = five.notna().all() and pd.notna(total)
        s = int(five.sum()) if complete else None
        diff = s - int(total) if complete else None
        note = "" if complete else "missing item; check not possible"
        if extras.get(src):
            note = "; ".join(filter(None, [note, "note has lines other than the five items: " + " | ".join(extras[src])]))
        checks.append({
            "source_file": src,
            "column": "comparative" if restated else "current",
            "year": year,
            "restated": restated,
            "basis": g["basis"].iloc[0],
            "sum_of_five_items": s,
            "total_production_cost": int(total) if pd.notna(total) else None,
            "difference": diff,
            "unit": "TRY",
            "passed": (diff == 0) if complete else False,
            "note": note,
        })
    return pd.DataFrame(checks).sort_values(["source_file", "column"], ascending=[True, False])


def main():
    all_rows, extras = [], {}
    for year in YEARS:
        rows, missing, extra_lines = extract_report(year)
        all_rows.extend(rows)
        extras[rows[0]["source_file"]] = extra_lines
        print(f"{year}: {len(rows) // 2} items" + (f" | NOT FOUND: {missing}" if missing else ""))

    df = pd.DataFrame(all_rows)
    df["value"] = df["value"].astype("Int64")
    df = df.sort_values(["source_file", "restated", "item"]).reset_index(drop=True)
    TABLE_OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(TABLE_OUT, index=False, encoding="utf-8")

    checks = build_checks(df, extras)
    checks["sum_of_five_items"] = checks["sum_of_five_items"].astype("Int64")
    checks["total_production_cost"] = checks["total_production_cost"].astype("Int64")
    checks["difference"] = checks["difference"].astype("Int64")
    CHECK_OUT.parent.mkdir(parents=True, exist_ok=True)
    checks.to_csv(CHECK_OUT, index=False, encoding="utf-8")

    print(f"\nTable: {TABLE_OUT.relative_to(ROOT)} ({len(df)} rows)")
    print(f"Check: {CHECK_OUT.relative_to(ROOT)} ({int(checks['passed'].sum())}/{len(checks)} passed)")


if __name__ == "__main__":
    main()
