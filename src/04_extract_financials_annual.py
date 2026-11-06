"""
04 – Extracts additional financial items from the annual (12-month) financial reports.

Source : Data/FinancialStatements/YYYY/Cimsa_YYYY_12.pdf (2016–2025)
Outputs:
  output/tables/financials_annual.csv          (long format; period = FY)
  output/checks/financials_annual_checks.csv   (segment total, revenue breakdown, profit split,
                                                revenue compared with cost_items_annual)

Two columns are read from each report: the current year (restated=False) and the comparative year (restated=True).
Unit: 2022 and earlier in TRY, 2023 and later in TRY thousand -> all in TRY (unit = TRY). FX rates are not converted.
Sign: expenses positive; for profit/loss items a loss is negative.
"""
from pathlib import Path
import re

import pandas as pd
import pdfplumber

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "Data" / "FinancialStatements"
TABLE_OUT = ROOT / "output" / "tables" / "financials_annual.csv"
CHECK_OUT = ROOT / "output" / "checks" / "financials_annual_checks.csv"
COST_ITEMS = ROOT / "output" / "tables" / "cost_items_annual.csv"
YEARS = range(2016, 2026)

BROKEN_CHARS = str.maketrans({"Ģ": "ş", "ġ": "Ş", "Ġ": "İ", "‟": "’"})  # 2016 font encoding
ASCII_MAP = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")

AMOUNT_RE = re.compile(r"^\(?\d{1,3}(?:\.\d{3})*\)?$")
MARKER_RE = re.compile(r"^(?:\(\d\)|\(\*+\)|\*+|\(\d\)\(\*\))$")
NOTEREF_RE = re.compile(r"^(?:\d{1,2}|\d+(?:[,/]\d+)+|\(?not:?\d+\)?)$", re.I)
RATE_RE = re.compile(r"^\d{1,3},\d{2,4}$")

# Segment columns (by report year; verified against the header text)
SEGMENT_COLUMNS = {
    **{y: ["cement", "readymix", "unallocated", "elimination", "total"] for y in range(2016, 2020)},
    **{y: ["cement", "readymix", "total"] for y in range(2020, 2024)},
    **{y: ["cement", "readymix", "building_materials", "total"] for y in (2024, 2025)},
}
SEGMENT_HEADER_KEYS = {"cement": "cimento", "readymix": "hazirbeton", "building_materials": "yapi",
                       "unallocated": "dagitilmamis", "elimination": "eliminasyon", "total": "toplam"}


# ---------------------------------------------------------------- text helpers

def key(text: str) -> str:
    """For label matching: fix the encoding, ASCII, lower case, no spaces."""
    return re.sub(r"\s+", "", text.translate(BROKEN_CHARS).translate(ASCII_MAP).lower())


def to_number(tok: str):
    if tok == "-":
        return 0.0
    neg = tok.startswith("(")
    v = float(tok.strip("()").replace(".", ""))
    return -v if neg else v


def parse_line(line: str):
    """Splits a line into (label key, amount tokens, whether a malformed token exists)."""
    toks = line.split()
    # Bullet at the start of the line ("- Ana ortaklık payları")
    if len(toks) > 1 and toks[0] == "-" and re.search(r"[a-zA-ZçğıöşüÇĞİÖŞÜ]", toks[1]):
        toks = toks[1:]
    label, nums, bad = [], [], False
    for t in toks:
        if MARKER_RE.match(t):
            continue
        if not nums and re.fullmatch(r"\d+\.", t):
            label.append(t)  # note heading number ("19. HASILAT")
            continue
        is_num = AMOUNT_RE.match(t) or t == "-" or NOTEREF_RE.match(t)
        if not nums and not is_num:
            if any(ch.isdigit() for ch in t) and not re.search(r"[a-zA-ZçğıöşüÇĞİÖŞÜ]", t):
                bad = True  # malformed number right after the label
                nums.append(t)
                continue
            label.append(t)
        elif is_num:
            nums.append(t)
        elif any(ch.isdigit() for ch in t):
            nums.append(t)
            bad = True
        else:
            break  # new text after the numbers (two-column line etc.)
    # Drop leading note numbers (keeping at least two amounts)
    while len(nums) > 2 and NOTEREF_RE.match(nums[0]) and "." not in nums[0]:
        nums = nums[1:]
    if bad:
        good = []
        for t in nums:
            if not (AMOUNT_RE.match(t) or t == "-"):
                break
            if "." in t or t == "-":
                good.append(t)
        nums = good
    else:
        nums = [t for t in nums if AMOUNT_RE.match(t) or t == "-"]
    return key(" ".join(label)), nums, bad


def numbers_only(line: str):
    toks = [t for t in line.split() if not MARKER_RE.match(t)]
    return toks if toks and all(AMOUNT_RE.match(t) or t == "-" for t in toks) else None


def values_at(lines, i, n=2):
    """Amounts of line i; if the label line has none, the 'numbers only' line above/below."""
    _, nums, bad = parse_line(lines[i])
    note = "line has an unreadable (malformed) number; only the reliable value was taken" if bad else ""
    if not nums and not bad:
        for j in (i - 1, i + 1):
            if 0 <= j < len(lines):
                no = numbers_only(lines[j])
                if no and len(no) >= n:
                    nums = no
                    note = "numbers on the line " + ("above" if j < i else "below") + " the label"
                    break
    vals = [to_number(t) for t in nums]
    if "-" in nums[:2]:
        note = "; ".join(filter(None, [note, "'-' in report (recorded as zero)"]))
    return vals, note


def find(lines, pattern, start=0, end=None, need_numbers=True):
    """Index of the first line whose label key fully matches the regex."""
    rx = re.compile(pattern)
    for i in range(start, len(lines) if end is None else min(end, len(lines))):
        lab, nums, bad = parse_line(lines[i])
        if lab and rx.fullmatch(lab):
            if not need_numbers or nums or bad or (i > 0 and numbers_only(lines[i - 1])) \
                    or (i + 1 < len(lines) and numbers_only(lines[i + 1])):
                return i
    return None


# ---------------------------------------------------------------- report reading

class Report:
    def __init__(self, year):
        self.year = year
        self.path = DATA_DIR / str(year) / f"Cimsa_{year}_12.pdf"
        self.rel = self.path.relative_to(ROOT).as_posix()
        self.basis = "nominal" if year <= 2022 else "tms29"
        self.factor = 1 if year <= 2022 else 1000
        with pdfplumber.open(self.path) as pdf:
            self.pages = [(p.extract_text() or "").split("\n") for p in pdf.pages]
        # all lines and page numbers
        self.lines, self.page_of = [], []
        for pno, ls in enumerate(self.pages, start=1):
            self.lines.extend(ls)
            self.page_of.extend([pno] * len(ls))
        self.rows = []

    def add(self, item, values, idx, note="", sign=1, unit="TRY", money=True):
        """values: [current, comparative]; missing value None."""
        page = self.page_of[idx] if idx is not None else None
        for k, (yr, restated) in enumerate([(self.year, False), (self.year - 1, True)]):
            v = values[k] if values is not None and k < len(values) else None
            if v is not None:
                v = v * sign * (self.factor if money else 1)
            notes = [n for n in [note] if n]
            if restated:
                notes.append(f"comparative column of the {self.year} report")
            if v is None:
                notes.append("not found / unreadable in report")
            self.rows.append({"year": yr, "period": "FY", "item": item, "value": v, "unit": unit,
                              "basis": self.basis, "restated": restated, "source_file": self.rel,
                              "source_page": page, "note": "; ".join(notes)})

    def grab(self, item, pattern, start=0, end=None, sign=1, transform=None, note="", required=True):
        i = find(self.lines, pattern, start, end)
        if i is None:
            if required:
                self.add(item, None, None, note)
            return None
        vals, vnote = values_at(self.lines, i)
        vals = vals[:2] + [None] * (2 - len(vals[:2]))
        if transform:
            vals = [transform(v) if v is not None else None for v in vals]
        self.add(item, vals, i, "; ".join(filter(None, [note, vnote])), sign=sign)
        return i


# ---------------------------------------------------------------- sections

def extract_pl(r: Report):
    # Income statement page
    start = None
    for pno, ls in enumerate(r.pages[:20], start=1):
        k = key(" ".join(ls))
        if "karveyazarar" in k and "esasfaaliyetkari" in k:
            start = r.page_of.index(pno)
            break
    if start is None:
        print(f"  WARNING {r.year}: income statement not found")
        return
    end = start + 80
    r.grab("revenue", r"hasilat", start, end, note="income statement")
    r.grab("operating_profit", r"esasfaaliyet(kari|zarari)[/()a-z]*", start, end)
    r.grab("finance_income", r"finansmangelirleri", start, end)
    r.grab("finance_costs", r"finansmangiderleri(\(-\))?", start, end, transform=abs)
    if r.basis == "tms29":
        r.grab("net_monetary_position", r"netparasalpozisyon(kari|zarari|kazanci|kaybi)[/()a-z]*", start, end)
    r.grab("discontinued_operations", r"durdurulanfaaliyetlerdonem[/()a-z]*", start, end, required=False)
    i = r.grab("net_income", r"donem(kari|zarari)(/?\(?(zarari|kari)\)?)?", start, end)
    if i is not None:
        r.grab("net_income_parent", r"-?anaortaklikpaylari", i, i + 6)
        r.grab("net_income_minority", r"-?kontrolgucuolmayanpaylar", i, i + 6)
    else:
        r.add("net_income_parent", None, None)
        r.add("net_income_minority", None, None)


def extract_revenue_note(r: Report):
    i0 = find(r.lines, r"\d+\.hasilat", need_numbers=False)
    if i0 is None:
        for it in ("revenue_domestic", "revenue_export", "revenue_deductions"):
            r.add(it, None, None)
        return
    end = i0 + 15
    r.grab("revenue_domestic", r"yurtici(satislar|satislari)", i0, end)
    r.grab("revenue_export", r"yurtdisi(satislar|satislari)", i0, end)
    a = find(r.lines, r"satisiskontolari(\(-\))?", i0, end)
    b = find(r.lines, r"(satismasraflarive)?digerindirimler(\(-\))?", i0, end)
    comps, total, idx = [], [0.0, 0.0], None
    for j, name in [(a, "sales discounts"), (b, r.lines[b].split("(")[0].strip() if b is not None else "")]:
        if j is None:
            continue
        vals, _ = values_at(r.lines, j)
        total = [total[k] + abs(vals[k]) for k in range(2)]
        comps.append(f"{name}: {abs(vals[0]) * r.factor:,.0f} / {abs(vals[1]) * r.factor:,.0f}")
        idx = j if idx is None else idx
    if comps:
        r.add("revenue_deductions", total, idx, "total = " + " + ".join(
            ["sales discounts", "other deductions"][:len(comps)]) + " (current / comparative TRY: " + "; ".join(comps) + ")")
    else:
        r.add("revenue_deductions", None, None)


def extract_segments(r: Report):
    cols = SEGMENT_COLUMNS[r.year]
    s0 = find(r.lines, r"\d+\.bolumleregoreraporlama", need_numbers=False)
    if s0 is None:
        print(f"  WARNING {r.year}: segment note not found")
        return
    done = set()
    for i in range(s0, min(s0 + 400, len(r.lines))):
        m = re.search(r"1ocak[-–]31aralik(20\d\d)", key(r.lines[i]))
        if not m:
            continue
        yr = int(m.group(1))
        if yr in done or yr not in (r.year, r.year - 1):
            continue
        h = find(r.lines, r"hasilat", i, i + 6)
        if h is None:
            continue
        restated = yr != r.year
        header_txt = key(" ".join(r.lines[max(s0, i - 3):i + 3]))
        missing_hdr = [c for c in cols if SEGMENT_HEADER_KEYS[c] not in header_txt]
        rows = {"revenue": h,
                "cost_of_sales": find(r.lines, r"satislarinmaliyeti(\(-\))?", h - 1, h + 4),
                "gross_profit": find(r.lines, r"brut(kar|zarar)[/()a-z]*", h, h + 6)}
        for kind, j in rows.items():
            vals, vnote = values_at(r.lines, j, n=len(cols)) if j is not None else ([], "")
            ok = len(vals) == len(cols)
            for c_idx, col in enumerate(cols):
                v = vals[c_idx] if ok else None
                if v is not None:
                    v = v * r.factor
                    if kind == "cost_of_sales":
                        v = -v if v else 0.0  # expense positive; the elimination line becomes negative
                notes = [vnote] if vnote else []
                if col == "elimination":
                    notes.append("inter-segment elimination (negative in revenue; negative in cost = cost reduction)")
                if col == "building_materials" and r.year == 2025:
                    notes.append("'Yapı Malzemeleri ve Paketleme' (building materials and packaging) in report")
                if v == 0 and vals and ok and r.lines[j].split().count("-"):
                    notes.append("'-' in report")
                if missing_hdr:
                    notes.append(f"WARNING: column name not found in header: {missing_hdr}")
                if not ok:
                    notes.append(f"column count mismatch (expected {len(cols)}, read {len(vals)})")
                if restated:
                    notes.append(f"comparative column of the {r.year} report")
                if v is None:
                    notes.append("not found / unreadable in report")
                r.rows.append({"year": yr, "period": "FY", "item": f"segment_{col}_{kind}", "value": v,
                               "unit": "TRY", "basis": r.basis, "restated": restated, "source_file": r.rel,
                               "source_page": r.page_of[j] if j is not None else None,
                               "note": "; ".join(notes)})
        done.add(yr)
    for yr in (r.year, r.year - 1):
        if yr not in done:
            print(f"  WARNING {r.year}: {yr} segment table not found")


def extract_cashflow_balance(r: Report):
    r.grab("depreciation_amortization_total", r"amortismanveitfagideriileilgiliduzeltme(ler)?", transform=abs,
           note="cash flow statement")
    r.grab("cash", r"nakitvenakitbenzerleri", note="statement of financial position (first occurrence)")
    r.grab("financial_debt_total", r"toplamfinansalborclar+",
           note="borrowings note; total including IFRS 16 lease liabilities")


def extract_fx_trade(r: Report):
    for item, label in [("fx_exports_try", "ihracat"), ("fx_imports_try", "ithalat")]:
        i = find(r.lines, label)
        if i is None:
            r.add(item, None, None)
            continue
        _, nums, _ = parse_line(r.lines[i])
        vals = [to_number(t) for t in nums]
        note = "effects of exchange rate changes note; TRY equivalent column"
        if len(vals) > 5:
            cur, prev = vals[0], max(vals[1:])
            note += "; comparative-year TRY equivalent is the largest value of the second block in the same line (TRY column)"
        else:
            cur = vals[0] if vals else None
            k = find(r.lines, label, i + 1)
            prev = to_number(parse_line(r.lines[k])[1][0]) if k is not None and parse_line(r.lines[k])[1] else None
            note += "; comparative year from a separate table (next line group)"
        r.add(item, [cur, prev], i, note)


def extract_enerjisa(r: Report):
    """Enerjisa purchases: total + electricity supply + 'Müşteri Çözümleri' (customer solutions; O&M service) separately."""
    i0 = None
    for i, l in enumerate(r.lines):
        if "iliskilitaraflardanyapilanmalvehizmetalimlari" in key(l):
            i0 = i
            break
    if i0 is None:
        for it in ("enerjisa_purchases", "enerjisa_electricity_purchases"):
            r.add(it, None, None)
        return
    groups = {"all": [[0.0, 0.0], []], "electricity": [[0.0, 0.0], []], "services": [[0.0, 0.0], []]}
    for j in range(i0 + 1, min(i0 + 30, len(r.lines))):
        line = r.lines[j]
        no = numbers_only(line)
        if no and len(no) >= 2:
            break  # table total line
        if "enerjisa" in key(line) and not line.strip().startswith(("(", "*")):
            _, nums, _ = parse_line(line)
            if len(nums) >= 2:
                vals = [to_number(t) for t in nums[:2]]
                name = re.split(r"\s\(|\s\d", line.strip())[0].strip()
                kind = "services" if "mustericozumleri" in key(line) else "electricity"
                for g in ("all", kind):
                    groups[g][0] = [groups[g][0][k] + vals[k] for k in range(2)]
                    groups[g][1].append(name)
    labels = {"all": ("enerjisa_purchases", "all Enerjisa lines"),
              "electricity": ("enerjisa_electricity_purchases",
                              "electricity supply lines (Toptan Satış / Enerji A.Ş. / Üretim Santralleri)"),
              "services": ("enerjisa_services_purchases",
                           "'Müşteri Çözümleri' (customer solutions) lines (solar/WHR O&M service; not electricity)")}
    for g, (item, desc) in labels.items():
        total, names = groups[g]
        if names:
            r.add(item, total, i0, f"purchases of goods and services from related parties; {desc}; lines summed: "
                  + " + ".join(names))
        elif g != "services":
            r.add(item, None, i0, f"no {desc} in the purchases table")


def extract_fx_rates(r: Report):
    i_avg = None
    for i, l in enumerate(r.lines):
        if "ortalamakurlar" in key(l):
            i_avg = i
            break
    specs = [("usd", r"(abddolari\(“usd”\)|usd\(“abddolari”\)|usd)/tl"),
             ("eur", r"(avro\(“eur”\)|eur)/tl")]
    for cur, pat in specs:
        rx = re.compile(pat)
        pe_idx = avg_idx = None
        if i_avg is not None:
            for j in range(i_avg - 1, max(i_avg - 15, 0), -1):
                if rx.fullmatch(key(" ".join(t for t in r.lines[j].split() if not RATE_RE.match(t)))):
                    pe_idx = j
                    break
            for j in range(i_avg + 1, min(i_avg + 8, len(r.lines))):
                if rx.fullmatch(key(" ".join(t for t in r.lines[j].split() if not RATE_RE.match(t)))):
                    avg_idx = j
                    break
        for kind, j in [("period_end", pe_idx), ("average", avg_idx)]:
            item = f"fx_rate_{cur}_{kind}"
            unit = f"TRY/{cur.upper()}"
            if j is None:
                r.add(item, None, None, "FX rate table not found", unit=unit, money=False)
                continue
            rates = [float(t.replace(",", ".")) for t in r.lines[j].split() if RATE_RE.match(t)]
            note = "the report's FX rate table"
            if kind == "period_end" and len(rates) >= 4:
                rates = [rates[0], rates[2]]
                note += "; period-end CBRT buying rate (the report gives buying/selling separately)"
            r.add(item, rates[:2], j, note, unit=unit, money=False)


# ---------------------------------------------------------------- checks

def build_checks(df: pd.DataFrame) -> pd.DataFrame:
    out = []
    key_cols = ["source_file", "year", "restated", "basis"]
    for k, g in df.groupby(key_cols):
        v = g.set_index("item")["value"]
        rev = v.get("revenue")

        def add(check, lhs, rhs, note=""):
            ok = pd.notna(lhs) and pd.notna(rhs)
            out.append({"check": check, **dict(zip(key_cols, k)), "lhs": lhs, "rhs": rhs,
                        "difference": (lhs - rhs) if ok else None,
                        "passed": bool(ok and abs(lhs - rhs) < 1.5 * (1000 if k[3] == "tms29" else 1)),
                        "note": note if ok else "; ".join(filter(None, [note, "one side missing; check not possible"]))})

        seg = g[g["item"].str.match(r"segment_.*_revenue$") & ~g["item"].str.contains("_total_")]
        add("a_segment_revenue_sum_vs_revenue", seg["value"].sum(min_count=1) if len(seg) else None, rev,
            "sum of segment revenues (incl. elimination/unallocated) vs income statement revenue")
        dom, exp, ded = v.get("revenue_domestic"), v.get("revenue_export"), v.get("revenue_deductions")
        add("b_domestic_plus_export_minus_deductions", (dom + exp - ded) if all(pd.notna([dom, exp, ded])) else None,
            rev, "domestic + export − deductions vs revenue")
        par, mino, ni = v.get("net_income_parent"), v.get("net_income_minority"), v.get("net_income")
        add("c_parent_plus_minority_vs_net_income", (par + mino) if pd.notna(par) and pd.notna(mino) else None, ni,
            "attributable to parent + non-controlling interests vs net income")

    # d) revenue compared with cost_items_annual
    if COST_ITEMS.exists():
        ci = pd.read_csv(COST_ITEMS)
        ci = ci[ci["item"] == "revenue"][["source_file", "year", "restated", "value"]].rename(columns={"value": "ci"})
        fr = df[df["item"] == "revenue"][key_cols + ["value"]]
        m = fr.merge(ci, on=["source_file", "year", "restated"], how="left")
        for _, row in m.iterrows():
            ok = pd.notna(row["value"]) and pd.notna(row["ci"])
            out.append({"check": "d_revenue_vs_cost_items_annual", "source_file": row["source_file"],
                        "year": row["year"], "restated": row["restated"], "basis": row["basis"],
                        "lhs": row["value"], "rhs": row["ci"],
                        "difference": (row["value"] - row["ci"]) if ok else None,
                        "passed": bool(ok and row["value"] == row["ci"]),
                        "note": "financials_annual revenue vs cost_items_annual revenue (same report, same column)"})
    res = pd.DataFrame(out)
    return res.sort_values(["check", "source_file", "restated"]).reset_index(drop=True)


def main():
    rows = []
    for y in YEARS:
        r = Report(y)
        extract_pl(r)
        extract_revenue_note(r)
        extract_segments(r)
        extract_cashflow_balance(r)
        extract_fx_trade(r)
        extract_enerjisa(r)
        extract_fx_rates(r)
        missing = sorted({x["item"] for x in r.rows if x["value"] is None and not x["restated"]})
        print(f"{y}: {len(r.rows)} rows" + (f" | empty in current year: {missing}" if missing else ""))
        rows.extend(r.rows)

    df = pd.DataFrame(rows)
    df["source_page"] = df["source_page"].astype("Int64")
    df = df.sort_values(["source_file", "restated", "item"]).reset_index(drop=True)
    TABLE_OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(TABLE_OUT, index=False, encoding="utf-8")

    checks = build_checks(df)
    CHECK_OUT.parent.mkdir(parents=True, exist_ok=True)
    checks.to_csv(CHECK_OUT, index=False, encoding="utf-8")
    print(f"\nTable: {TABLE_OUT.relative_to(ROOT)} ({len(df)} rows)")
    for c, g in checks.groupby("check"):
        print(f"Check {c}: {int(g['passed'].sum())}/{len(g)} passed")


if __name__ == "__main__":
    main()
