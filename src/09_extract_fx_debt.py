"""
09 – FX position and debt data (H1 / Branch 1).

Source : Data/FinancialStatements/YYYY/Cimsa_YYYY_12.pdf (2016–2025)
Outputs:
  output/tables/fx_debt_annual.csv     (long format; period = FY)
  output/checks/fx_debt_checks.csv     (currency split totals, comparison with total financial debt,
                                        FX position arithmetic, rate/label consistency)

Three notes are read:
  1. Borrowings – breakdown of loans by currency (TRY equivalent, original balance, short/long term).
  2. Effects of exchange rate changes – foreign currency assets / liabilities / derivatives / net position (TRY and USD, EUR original).
     This table does not include the national currencies of subsidiaries abroad (only companies with TRY functional currency).
  3. Sensitivity to FX risk – effect of a 10% FX change on profit/loss (foreign currency appreciation column).

Unit: 2022 and earlier in TRY, 2023 and later in TRY thousand -> all in TRY (unit = TRY). Original FX amounts are reported
in "thousands" from 2023 -> converted to units (unit = USD / EUR). Basis: 2016–2022 reports nominal, 2023+ tms29.
From each report the current year (restated=False) and the comparative columns (restated=True) are taken. The
"1 Ocak 2022" (1 January 2022) column in the 2023 report is stored as the 2021 year-end opening balance (year=2021, restated=True).
Sign: assets and liabilities as positive magnitudes; derivatives and net position with the reported sign (positive = net asset);
sensitivity: positive = profit increase.
"""
import importlib.util
from pathlib import Path
import re

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TABLE_OUT = ROOT / "output" / "tables" / "fx_debt_annual.csv"
CHECK_OUT = ROOT / "output" / "checks" / "fx_debt_checks.csv"
FIN_IN = ROOT / "output" / "tables" / "financials_annual.csv"
FX_IN = ROOT / "output" / "tables" / "fx_rates_annual.csv"
YEARS = range(2016, 2026)

_spec = importlib.util.spec_from_file_location("extract_fin", ROOT / "src" / "04_extract_financials_annual.py")
fin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fin)
key, Report = fin.key, fin.Report

AMOUNT_RE = re.compile(r"^\(?\d{1,3}(?:\.\d{3})*\)?$")
DATE_RE = re.compile(r"(31)\s*Aral[ıi]k\s*(\d{4})|(1)\s*Ocak\s*(\d{4})")
CUR_KEYS = {"try": "try", "tl": "try", "eur": "eur", "avro": "eur", "euro": "eur", "usd": "usd",
            "diger": "other", "gbp": "other", "chf": "other"}
LABEL_TOL = 0.04       # accepted deviation between original × rate and the TRY equivalent (accrued interest, rate date)
LABEL_TOL_TRY = 0.25   # for TRY loans the gap between original principal and accrued balance can be large
RECLASS_MIN_DEV = 0.10 # a label is corrected only if the report's label deviates this much and exactly one other currency fits
NOTE_FX_SCOPE = ("Position covers only companies with TRY functional currency; report note (translated): "
                 "'the national currencies of subsidiaries abroad ... are not included in the foreign currency position'")


# ---------------------------------------------------------------- helpers

def clean_tok(t: str) -> str:
    t = t.replace("--", "-")
    t = re.sub(r"\)+$", ")", t)
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+\.", t):  # trailing dot in "15.493.798."
        t = t[:-1]
    return t


def num(t):
    return None if t is None else fin.to_number(t)


def date_of(text):
    """First date in the text -> (year, is opening). '1 Ocak 2022' -> (2021, True)."""
    m = DATE_RE.search(text)
    if not m:
        return None
    if m.group(1):
        return int(m.group(2)), False
    return int(m.group(4)) - 1, True


def column_role(report_year, year, opening):
    restated = not (year == report_year and not opening)
    note = "1 January opening balance (prior year-end)" if opening else ""
    return restated, note


def orig_factor(r: Report) -> int:
    k = key(" ".join(r.lines[:400]))
    return 1000 if "tldisindakiparabirimleri" in k and "binolarak" in k else 1


def period_end_rates():
    fx = pd.read_csv(FX_IN)
    fx = fx[(fx["period"] == "FY") & fx["item"].str.endswith("period_end")]
    return {(int(r["year"]), r["item"].split("_")[1]): r["value"] for _, r in fx.iterrows()}


class Rows:
    def __init__(self, r: Report):
        self.r, self.rows = r, []

    def add(self, year, restated, item, value, unit, idx, note=""):
        notes = [n for n in [note] if n]
        if restated:
            notes.append(f"comparative column of the {self.r.year} report")
        if value is None:
            notes.append("not found / unreadable in report")
        self.rows.append({"year": year, "period": "FY", "item": item, "value": value, "unit": unit,
                          "basis": self.r.basis, "restated": restated, "source_file": self.r.rel,
                          "source_page": self.r.page_of[idx] if idx is not None else None,
                          "note": "; ".join(notes)})


# ---------------------------------------------------------------- 1. borrowings

def parse_loan_row(line, has_orig):
    """Loan line -> (currency, original, short, long, total, note) or None."""
    toks = [clean_tok(t) for t in line.split()]
    ci = next((i for i, t in enumerate(toks) if key(t) in CUR_KEYS), None)
    if ci is None:
        return None
    cur = CUR_KEYS[key(toks[ci])]
    after = toks[ci + 1:]
    amounts = [t for t in after if AMOUNT_RE.match(t) or t == "-"]
    garbled = [t for t in after if any(c.isdigit() for c in t) and not (AMOUNT_RE.match(t) or t == "-")
               and not re.search(r"[%,a-zA-Z+]", t)]
    k = 4 if has_orig else 3
    if len(amounts) < k - (1 if garbled else 0):
        return None

    def valid(o, s, l, tot):
        return abs((s or 0) + (l or 0) - (tot or 0)) <= max(2, abs(tot or 0) * 1e-6)

    note = ""
    if not garbled:
        vals = [num(t) for t in amounts[-k:]]
        orig, (s, l, tot) = (vals[0], vals[1:]) if has_orig else (None, vals)
        if not valid(orig, s, l, tot):
            # split number ("4 5.749.600"): merge a 1–3 digit token with the following amount
            merged, i = [], 0
            while i < len(after):
                t = after[i]
                if re.fullmatch(r"\d{1,3}", t) and i + 1 < len(after) and \
                        re.fullmatch(r"\d{1,3}(?:\.\d{3})+", t + after[i + 1]) and \
                        len(t + after[i + 1].split(".")[0]) <= 3:
                    merged.append(t + after[i + 1]); i += 2
                else:
                    merged.append(t); i += 1
            am2 = [t for t in merged if AMOUNT_RE.match(t) or t == "-"]
            vals = [num(t) for t in am2[-k:]]
            orig, (s, l, tot) = (vals[0], vals[1:]) if has_orig else (None, vals)
            note = "split number merged"
            if not valid(orig, s, l, tot):
                return cur, orig, s, l, tot, "WARNING: short + long ≠ total"
        return cur, orig, s, l, tot, note
    # malformed token: original, short and total are reliable; long = total − short
    vals = [num(t) for t in amounts]
    orig = vals[0] if has_orig else None
    s, tot = vals[1 if has_orig else 0], vals[-1]
    return cur, orig, s, tot - s, tot, "malformed number in line; long-term part = total − short-term"


def extract_loans(r: Report, out: Rows, rates, ofac, checks):
    titles = [i for i, l in enumerate(r.lines)
              if re.search(r"kredilerin(ve\S*)?detay", key(l)) and "geriodeme" not in key(l)]
    for ti in titles:
        d = date_of(r.lines[ti])
        if d is None:
            continue
        year, opening = d
        restated, role_note = column_role(r.year, year, opening)
        # header lines and rows
        j, header, loans, total_row, total_idx = ti + 1, [], [], None, None
        while j < min(ti + 40, len(r.lines)):
            line = r.lines[j]
            if j > ti + 1 and re.search(r"kredilerin(ve\S*)?detay", key(line)):
                break
            no = fin.numbers_only(line)
            if loans and no and len(no) == 3:
                total_row, total_idx = [num(t) for t in no], j
                break
            p = parse_loan_row(line, "orijinal" in key(" ".join(header)))
            if p and not loans and not header:
                header = []
            if p:
                loans.append((j, *p))
            elif not loans:
                header.append(line)
            j += 1
        has_orig = "orijinal" in key(" ".join(header))
        if not loans:
            continue
        # currency label check (skipped for IAS 29 comparative columns because they are re-indexed)
        rate_year = year
        check_labels = has_orig and not (r.basis == "tms29" and restated)
        agg = {c: {"try": 0.0, "short": 0.0, "long": 0.0, "orig": 0.0, "orig_ok": True, "n": 0}
               for c in ["try", "eur", "usd", "other"]}
        row_notes = []
        for (idx, cur, orig, s, l, tot, note) in loans:
            if note:
                row_notes.append(f"p.{r.page_of[idx]} {cur.upper()} {tot:,.0f}: {note}")
                checks.append({"check": "loan_row_parse", "year": year, "restated": restated, "source_file": r.rel,
                               "status": "warning" if note.startswith("WARNING") else "info", "value_a": tot,
                               "value_b": None, "diff": None, "detail": f"{cur.upper()} line: {note}"})
            if check_labels and orig:
                implied = tot * r.factor / (orig * ofac)
                exp = {"try": 1.0, "usd": rates.get((rate_year, "usd")), "eur": rates.get((rate_year, "eur"))}
                dev = {c: abs(implied / v - 1) for c, v in exp.items() if v}
                fits = {c: d <= (LABEL_TOL_TRY if c == "try" else LABEL_TOL) for c, d in dev.items()}
                if cur in fits and not fits[cur]:
                    alt = [c for c, ok in fits.items() if ok and c != cur]
                    detail = (f"{cur.upper()} in report; TRY/original = {implied:.4f}; expected "
                              + ", ".join(f"{c.upper()} {v:.4f}" for c, v in exp.items() if v))
                    if len(alt) == 1 and dev[cur] > RECLASS_MIN_DEV:
                        detail += f" -> reclassified as {alt[0].upper()} (possible label error in the report)"
                        row_notes.append(f"p.{r.page_of[idx]} label {cur.upper()}->{alt[0].upper()} corrected")
                        cur = alt[0]
                        status = "warning (corrected)"
                    else:
                        status = "warning (not corrected)"
                        detail += ("; label kept (deviation small, or more than one / no currency fits; "
                                   "possibly accrued interest or a report error)")
                    checks.append({"check": "loan_currency_label", "year": year, "restated": restated,
                                   "source_file": r.rel, "status": status, "value_a": tot, "value_b": orig,
                                   "diff": None, "detail": detail})
            a = agg[cur]
            a["try"] += tot; a["short"] += s or 0; a["long"] += l or 0; a["n"] += 1
            if orig is None:
                a["orig_ok"] = False
            else:
                a["orig"] += orig * ofac
        f = r.factor
        base_note = "; ".join(filter(None, ["borrowings note, breakdown of loans by currency",
                                             role_note] + row_notes))
        first = loans[0][0]
        for c, a in agg.items():
            tag = "try" if c == "try" else f"{c}_try"
            v = a["try"] * f if a["n"] else 0.0
            out.add(year, restated, f"debt_{tag}", v, "TRY", first, base_note)
            out.add(year, restated, f"debt_{tag}_short", a["short"] * f if a["n"] else 0.0, "TRY", first, base_note)
            out.add(year, restated, f"debt_{tag}_long", a["long"] * f if a["n"] else 0.0, "TRY", first, base_note)
        for c in ["eur", "usd"]:
            a = agg[c]
            if not has_orig:
                v, n = None, "no original balance column in report"
            elif a["n"] == 0:
                v, n = 0.0, ""
            else:
                v, n = (a["orig"] if a["orig_ok"] else None), ""
            out.add(year, restated, f"debt_{c}_original", v, c.upper(), first,
                    "; ".join(filter(None, [base_note, n])))
        sums = [sum(a[k] for a in agg.values()) for k in ("short", "long", "try")]
        if total_row:
            for lab, v in zip(["bank_loans_short_try", "bank_loans_long_try", "bank_loans_total_try"], total_row):
                out.add(year, restated, lab, v * f, "TRY", total_idx,
                        "; ".join(filter(None, ["total line of the loan table", role_note])))
            diff = sums[2] - total_row[2]
            checks.append({"check": "loan_rows_sum_vs_table_total", "year": year, "restated": restated,
                           "source_file": r.rel, "status": "passed" if abs(diff) <= 5 else "warning",
                           "value_a": sums[2] * f, "value_b": total_row[2] * f, "diff": diff * f,
                           "detail": f"sum of rows vs table total line (p.{r.page_of[total_idx]})"})
        else:
            checks.append({"check": "loan_rows_sum_vs_table_total", "year": year, "restated": restated,
                           "source_file": r.rel, "status": "warning", "value_a": sums[2] * f, "value_b": None,
                           "diff": None, "detail": "table total line not found"})
    # Financial debt excluding IFRS 16 (2019+): for the lease difference check
    # the label is searched directly here because parse_line takes "16" for a note number
    i = next((k for k, l in enumerate(r.lines) if key(l).startswith("tfrs16haricfinansalborclar")), None)
    if i is not None:
        tail = re.split(r"bor[çc]lar", r.lines[i], flags=re.I)[-1]
        vals = [num(clean_tok(t)) for t in tail.split() if AMOUNT_RE.match(clean_tok(t))]
        vnote = ""
        cols = [(r.year, False, "")] + [(r.year - 1, True, "")]
        if r.year == 2023 and len(vals) >= 3:
            cols = [(2023, False, ""), (2022, True, ""), (2021, True, "1 January 2022 opening balance")]
        for (y, rs, n), v in zip(cols, vals):
            out.add(y, rs, "debt_ex_lease_try", v * r.factor, "TRY", i,
                    "; ".join(filter(None, ["borrowings note, 'TFRS 16 hariç finansal borçlar' (financial debt excl. IFRS 16)", n, vnote])))


# ---------------------------------------------------------------- 2. effects of exchange rate changes

FX_ROWS = [  # (item, label key regex, sign rule)
    ("fx_assets_total", r"^toplamvarliklar", "abs"),
    ("fx_financial_liabilities", r"^finansalyukumlulukler", "abs"),
    ("fx_liabilities_total", r"^toplamyukumlulukler", "abs"),
    ("fx_position_before_derivatives", r"^(yabancıparavarlik|yabancıparavarlık|yabanciparavarlik|parasalkalemlernet)", "raw"),
    ("fx_derivatives_net", r"^(bilancodisiturev|turevfinansalaraclarinnet)", "raw"),
    ("fx_net_position", r"^(net|pnoezt)\S*(pozisyonu|\(yukumluluk\)|yukumluluk)", "raw"),
]
FX_UNITS = {"try": "TRY", "usd": "USD", "eur": "EUR"}


def fx_header(line):
    """'TL karşılığı ABD Doları AVRO GBP Diğer TL karşılığı ...' -> list of columns for each date."""
    parts = re.split(r"TL\s*karşılığı", line.translate(fin.BROKEN_CHARS), flags=re.I)[1:]
    cols = []
    for p in parts:
        k = key(p)
        found = []
        for m in re.finditer(r"abd|dolar|avro|gbp|diger", k):
            c = {"abd": "usd", "dolar": "usd", "avro": "eur", "gbp": "gbp", "diger": "other"}[m.group()]
            if c not in found:
                found.append(c)
        cols.append(["try"] + found)
    return cols


def row_tokens(line):
    """Amount cells after the label; a malformed cell keeps its position as None."""
    toks = [clean_tok(t) for t in line.split()]
    cells, started = [], False
    for t in toks:
        if AMOUNT_RE.match(t) or t == "-":
            cells.append(t); started = True
        elif started and any(c.isdigit() for c in t):
            cells.append(None)
    return cells


def extract_fx_position(r: Report, out: Rows, ofac, checks):
    start = next((i for i, l in enumerate(r.lines) if re.match(r"^\d+\.kurdegisimininetkileri$", key(l))), None)
    if start is None:
        for item, _, _ in FX_ROWS:
            for c, u in FX_UNITS.items():
                out.add(r.year, False, f"{item}_{c}", None, u, None, "effects of exchange rate changes note not found")
        return
    end = next((i for i in range(start, min(start + 400, len(r.lines))) if "duyarlilik" in key(r.lines[i])),
               start + 200)
    sec = list(range(start, end))
    scope_note = NOTE_FX_SCOPE if "ulusalparabirim" in key(" ".join(r.lines[i] for i in sec)) else \
        "WARNING: note on subsidiaries abroad not found"
    # date blocks: date line + column header
    blocks = []
    for i in sec:
        line = r.lines[i]
        k = key(line)
        if "karsiligi" in k and "tl" in k:
            # nearest date line(s) above
            dates = []
            for j in range(i - 1, max(start, i - 4), -1):
                ds = [(int(m.group(2)), False) if m.group(1) else (int(m.group(4)) - 1, True)
                      for m in DATE_RE.finditer(r.lines[j])]
                if ds and len(re.sub(r"[\d\s]|Aral[ıi]k|Ocak", "", r.lines[j])) < 5:
                    dates = ds
                    break
            blocks.append((i, dates, fx_header(line)))
    if not blocks:
        checks.append({"check": "fx_position_parse", "year": r.year, "restated": False, "source_file": r.rel,
                       "status": "warning", "value_a": None, "value_b": None, "diff": None,
                       "detail": "column header not found"})
        return
    for bi, (hi, dates, colsets) in enumerate(blocks):
        bend = blocks[bi + 1][0] - 3 if bi + 1 < len(blocks) else end
        if len(dates) != len(colsets):
            checks.append({"check": "fx_position_parse", "year": r.year, "restated": False, "source_file": r.rel,
                           "status": "warning", "value_a": None, "value_b": None, "diff": None,
                           "detail": f"p.{r.page_of[hi]}: number of dates ({len(dates)}) ≠ column groups ({len(colsets)})"})
            continue
        found = {}
        for item, pattern, _ in FX_ROWS:
            for i in range(hi + 1, bend):
                lab, _, _ = fin.parse_line(r.lines[i])
                if lab and re.match(pattern, lab):
                    if item == "fx_net_position" and lab.startswith(("parasal", "yabanci", "yabancı")):
                        continue
                    # amounts on the same line or on the next two lines
                    for j in (i, i + 1, i + 2):
                        if j >= bend:
                            break
                        if j > i:
                            nk = key(r.lines[j])
                            # the line below must contain only numbers or be the continuation of the label
                            if not (fin.numbers_only(r.lines[j]) or nk.startswith(("(yukumluluk)", "pozisyonu"))):
                                continue
                        cells = row_tokens(r.lines[j])
                        if len(cells) >= len(colsets[0]) - 1:
                            found[item] = (j, cells)
                            break
                    break
        ncols = [len(c) for c in colsets]
        for item, rule in [(it, rl) for it, _, rl in FX_ROWS]:
            if item not in found:
                for (y, op), cols in zip(dates, colsets):
                    rs, rn = column_role(r.year, y, op)
                    if item == "fx_position_before_derivatives":
                        continue  # this line is absent in some years; checked arithmetically
                    for c, u in FX_UNITS.items():
                        out.add(y, rs, f"{item}_{c}", None, u, hi, "; ".join(filter(None, [rn, scope_note])))
                continue
            j, cells = found[item]
            note_cells = ""
            if len(cells) == sum(ncols):
                groups, pos = [], 0
                for n in ncols:
                    groups.append(cells[pos:pos + n]); pos += n
            elif len(dates) == 2 and len(cells) == sum(ncols) - 1:
                groups = [cells[:ncols[0]], cells[-ncols[1]:]]
                note_cells = "one cell missing in line; first cells taken for the current column, last cells for the prior period"
            else:
                groups = [None] * len(dates)
                note_cells = f"cell count mismatch ({len(cells)} / {sum(ncols)})"
            for (y, op), cols, g in zip(dates, colsets, groups):
                rs, rn = column_role(r.year, y, op)
                for c, u in FX_UNITS.items():
                    v, extra = None, note_cells
                    if g is not None and c in cols and cols.index(c) < len(g):
                        t = g[cols.index(c)]
                        if t is None:
                            extra = "; ".join(filter(None, [extra, "cell read garbled"]))
                        else:
                            v = num(t) * (r.factor if c == "try" else ofac)
                            if rule == "abs":
                                v = abs(v)
                    out.add(y, rs, f"{item}_{c}", v, u, j,
                            "; ".join(filter(None, ["effects of exchange rate changes note", rn, extra, scope_note])))


# ---------------------------------------------------------------- 3. sensitivity

SENS_ROWS = [("fx_sensitivity_usd_pl_try", r"dolar.*netetki"), ("fx_sensitivity_eur_pl_try", r"avro.*netetki"),
             ("fx_sensitivity_gbp_pl_try", r"sterlin.*netetki"), ("fx_sensitivity_other_pl_try", r"diger.*netetki"),
             ("fx_sensitivity_total_pl_try", r"^toplam\(")]


def extract_sensitivity(r: Report, out: Rows):
    heads = [i for i, l in enumerate(r.lines) if "degerkazanmasi" in key(l) and date_of(l)]
    if not heads:
        for item, _ in SENS_ROWS:
            out.add(r.year, False, item, None, "TRY", None, "sensitivity table not found")
        return
    for hn, hi in enumerate(heads):
        y, op = date_of(r.lines[hi])
        rs, rn = column_role(r.year, y, op)
        stop = heads[hn + 1] if hn + 1 < len(heads) else hi + 30
        for item, pattern in SENS_ROWS:
            hit = None
            for i in range(hi + 1, min(stop, len(r.lines))):
                k = key(r.lines[i])
                k = re.sub(r"^\d+-", "", k)
                if re.search(pattern, k):
                    hit = i
                    break
            if hit is None:
                if item in ("fx_sensitivity_usd_pl_try", "fx_sensitivity_eur_pl_try", "fx_sensitivity_total_pl_try"):
                    out.add(y, rs, item, None, "TRY", hi, "; ".join(filter(None, ["FX risk sensitivity table", rn])))
                continue
            line = r.lines[hit]
            m = list(re.finditer(r"\(\s*\d+(?:\s*\+\s*\d+)+\s*\)", line))
            tail = line[m[-1].end():] if m else line
            cells = re.findall(r"\(\d{1,3}(?:\.\d{3})*\)|\d{1,3}(?:\.\d{3})*|-", tail.replace(" ", ""))
            v = num(cells[0]) * r.factor if cells else None
            note = "FX risk sensitivity table; 10% foreign currency appreciation, effect on profit/loss"
            if len(cells) >= 2 and cells[1] != "-" and num(cells[1]) != -num(cells[0]):
                note += "; WARNING: depreciation column is not symmetric"
            out.add(y, rs, item, v, "TRY", hit, "; ".join(filter(None, [note, rn])))


# ---------------------------------------------------------------- checks (table level)

def build_checks(df: pd.DataFrame, checks: list) -> pd.DataFrame:
    finan = pd.read_csv(FIN_IN)
    debt_tot = finan[finan["item"] == "financial_debt_total"]
    g = df.pivot_table(index=["year", "restated", "source_file"], columns="item", values="value", aggfunc="first")
    for (y, rs, src), row in g.iterrows():
        # (a1) sum of the currency split = loan table total
        cur_sum = sum(row.get(f"debt_{t}", 0) or 0 for t in ["try", "eur_try", "usd_try", "other_try"])
        if pd.notna(row.get("bank_loans_total_try")):
            d = cur_sum - row["bank_loans_total_try"]
            checks.append({"check": "currency_split_vs_loans_total", "year": y, "restated": rs, "source_file": src,
                           "status": "passed" if abs(d) <= 5000 else "warning", "value_a": cur_sum,
                           "value_b": row["bank_loans_total_try"], "diff": d,
                           "detail": "TRY+EUR+USD+other = loan table total (rounding tolerance TRY 5,000)"})
        # (a2) comparison with total financial debt; difference = leases (IFRS 16)
        ft = debt_tot[(debt_tot["year"] == y) & (debt_tot["restated"] == rs) & (debt_tot["source_file"] == src)]
        ex = row.get("debt_ex_lease_try")
        if len(ft):
            total = ft["value"].iloc[0]
            d = total - cur_sum
            lease_note = ""
            if pd.notna(ex):
                lease_note = (f"; financial debt excl. IFRS 16 {ex:,.0f} (difference to the currency split total {cur_sum - ex:,.0f}); "
                              f"difference = lease liabilities TRY {total - ex:,.0f}")
                ok = abs(cur_sum - ex) <= 5000
            else:
                lease_note = "; no IFRS 16 lease line in report (before 2019)"
                ok = abs(d) <= 5000
            checks.append({"check": "currency_split_vs_financial_debt_total", "year": y, "restated": rs,
                           "source_file": src, "status": "passed" if ok else "warning", "value_a": cur_sum,
                           "value_b": total, "diff": d,
                           "detail": "currency split total vs financials_annual financial_debt_total" + lease_note})
        elif cur_sum:
            checks.append({"check": "currency_split_vs_financial_debt_total", "year": y, "restated": rs,
                           "source_file": src, "status": "info", "value_a": cur_sum, "value_b": None, "diff": None,
                           "detail": "no financial_debt_total in financials_annual for this column"
                                     + (f"; financial debt excl. IFRS 16 {ex:,.0f} (difference {cur_sum - ex:,.0f})" if pd.notna(ex) else "")})
        # (b) assets − liabilities (+ derivatives) = net position
        for c in ["try", "usd", "eur"]:
            a, l = row.get(f"fx_assets_total_{c}"), row.get(f"fx_liabilities_total_{c}")
            dv, net = row.get(f"fx_derivatives_net_{c}"), row.get(f"fx_net_position_{c}")
            pre = row.get(f"fx_position_before_derivatives_{c}")
            if pd.isna(a) or pd.isna(l):
                continue
            # TRY: TRY-thousand rounding; original: 2023+ rounding to thousand units
            report_year = int(re.search(r"(\d{4})_12", src).group(1))
            tol = 5000 if c == "try" else (2000 if report_year >= 2023 else 5)
            if pd.notna(pre):
                d = (a - l) - pre
                checks.append({"check": f"fx_assets_minus_liabilities_{c}", "year": y, "restated": rs,
                               "source_file": src, "status": "passed" if abs(d) <= tol else "warning",
                               "value_a": a - l, "value_b": pre, "diff": d,
                               "detail": "assets − liabilities = foreign currency position before derivatives"})
            if pd.notna(net):
                if pd.isna(dv):
                    checks.append({"check": f"fx_net_position_arithmetic_{c}", "year": y, "restated": rs,
                                   "source_file": src, "status": "could not be checked", "value_a": None,
                                   "value_b": net, "diff": None,
                                   "detail": "arithmetic check not possible because the derivative cell was read garbled"})
                    continue
                calc = a - l + dv
                d = calc - net
                detail = "assets − liabilities + off-balance-sheet derivatives = net foreign currency position"
                status = "passed" if abs(d) <= tol else "warning"
                if status == "warning" and abs(a - l - dv - net) <= tol:
                    detail += ("; the report shows the derivative as positive but the net position is reached by SUBTRACTING it "
                               "(assets − liabilities − derivatives = net); the derivative value is kept as reported")
                checks.append({"check": f"fx_net_position_arithmetic_{c}", "year": y, "restated": rs,
                               "source_file": src, "status": status, "value_a": calc, "value_b": net, "diff": d,
                               "detail": detail})
        # (c) TRY equivalent ≈ USD × rate + EUR × rate (excluding GBP and other; for information)
    out = pd.DataFrame(checks)
    return out


def fx_tl_consistency(df: pd.DataFrame, rates) -> list:
    rows = []
    g = df.pivot_table(index=["year", "restated", "source_file", "basis"], columns="item", values="value", aggfunc="first")
    for (y, rs, src, basis), row in g.iterrows():
        if basis == "tms29" and rs:
            continue  # the comparative column is re-indexed, so it does not match the rate
        ru, re_ = rates.get((y, "usd")), rates.get((y, "eur"))
        for item in ["fx_assets_total", "fx_financial_liabilities", "fx_liabilities_total"]:
            tl, u, e = row.get(f"{item}_try"), row.get(f"{item}_usd"), row.get(f"{item}_eur")
            if any(pd.isna(v) for v in (tl, u, e)) or not tl:
                continue
            calc = u * ru + e * re_
            dev = calc / tl - 1
            rows.append({"check": "fx_tl_vs_original_times_rate", "year": y, "restated": rs, "source_file": src,
                         "status": "passed" if abs(dev) <= 0.10 else "warning", "value_a": calc, "value_b": tl,
                         "diff": calc - tl,
                         "detail": f"{item}: USD×{ru:.4f} + EUR×{re_:.4f} vs TRY equivalent; deviation {dev*100:.1f}% "
                                   "(GBP and other currencies not included; tolerance 10%)"})
    return rows


def main():
    rates = period_end_rates()
    all_rows, checks = [], []
    for y in YEARS:
        r = Report(y)
        out = Rows(r)
        ofac = orig_factor(r)
        extract_loans(r, out, rates, ofac, checks)
        extract_fx_position(r, out, ofac, checks)
        extract_sensitivity(r, out)
        all_rows += out.rows
        print(f"  {y}: {len(out.rows)} rows (original FX multiplier {ofac})")
    df = pd.DataFrame(all_rows)
    df = df.sort_values(["year", "restated", "source_file", "item"]).reset_index(drop=True)
    ck = build_checks(df, checks)
    ck = pd.concat([ck, pd.DataFrame(fx_tl_consistency(df, rates))], ignore_index=True)
    missing = df[df["value"].isna()]
    for _, m in missing.iterrows():
        ck.loc[len(ck)] = {"check": "missing_value", "year": m["year"], "restated": m["restated"],
                           "source_file": m["source_file"], "status": "empty", "value_a": None, "value_b": None,
                           "diff": None, "detail": f"{m['item']}: {m['note']}"}
    TABLE_OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(TABLE_OUT, index=False, encoding="utf-8")
    CHECK_OUT.parent.mkdir(parents=True, exist_ok=True)
    ck.to_csv(CHECK_OUT, index=False, encoding="utf-8")
    print(f"Table: {TABLE_OUT.relative_to(ROOT)} ({len(df)} rows, empty {len(missing)})")
    print(f"Check: {CHECK_OUT.relative_to(ROOT)} ({len(ck)} rows)")
    print(ck["status"].value_counts().to_string())


if __name__ == "__main__":
    main()
