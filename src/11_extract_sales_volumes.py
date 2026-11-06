"""
11 – Sales volumes (Çimsa) and sector data (Turkey) – from the integrated annual reports.

Source : Data/IntegratedReports/2016.pdf … 2025.pdf
Outputs:
  output/tables/sales_volumes_candidates.csv  (all candidate values, with source page and raw text; no selection)
  output/tables/sales_volumes_annual.csv      (annual table under the "own report" rule; ton / m3)
  output/checks/sales_volumes_checks.csv      (year-on-year change > 30%; large gap between production and sales)

Method (same approach as 02_extract_esg_kpis.py and 03_build_esg_kpis_annual.py):
1. Çimsa sales – performance tables:
   * 2016–2018: lines of the form "Gri Çimento Satışı (ton) a b" (grey cement sales).
   * 2019–2025: after the "Toplam Satış (Miktarı)" (total sales volume) heading, up to the "Ekonomik Değer / Net Satışlar" line,
     product labels (grey, white, calcium aluminate cement, clinker, ready-mix concrete; 2024+ total "çimento").
     In two-column page layouts the label can be in the middle of the line; the numbers right after the label are read.
   * Years are assigned from the "YYYY YYYY" header above the line on the same page; if the number count does not match
     the header, year is blank and year_uncertain = True.
2. Sector data – narrative text: "X milyon ton" near the phrases "çimento üretimi", "klinker üretimi", "yurt içi çimento
   tüketimi/satışı", "çimento ihracatı", "klinker ihracatı", "çimento ve klinker ihracatı" (cement/clinker production,
   domestic consumption/sales, exports). The period phrase (first eleven months, January–November …), year and source
   (TÇMB, TürkÇimento, exporters' associations) are read from the raw text. Chart values (e.g. the sector chart in the
   2016 report) are not read.
3. Annual table: if the value's year = report year it is the "own report" value (restated = False); values from later
   reports are restated = True. If the own report has no value, the following report's value is used as restated.
   For the sector, a full-year value in the same report is preferred over a partial year; partial-year values are flagged.
"""
from pathlib import Path
import re

import pandas as pd
import pdfplumber

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "Data" / "IntegratedReports"
CAND_OUT = ROOT / "output" / "tables" / "sales_volumes_candidates.csv"
TABLE_OUT = ROOT / "output" / "tables" / "sales_volumes_annual.csv"
CHECK_OUT = ROOT / "output" / "checks" / "sales_volumes_checks.csv"
ESG_IN = ROOT / "output" / "tables" / "esg_kpis_annual.csv"
YEARS = range(2016, 2026)
YOY_FLAG = 30.0
GAP_FLAG = 15.0          # production–sales gap threshold (%)
RESTATE_TOL = 0.005

ASCII_MAP = str.maketrans("çğıöşüÇĞİÖŞÜâÂ’'", "cgiosuCGIOSUaA  ")
UNIT_FACTOR = {"ton": 1.0, "bin ton": 1e3, "milyon ton": 1e6, "m3": 1.0, "milyon m3": 1e6}
INLINE_MULT = {"bin": 1e3, "milyon": 1e6}

PRODUCTS = [  # (item, label regex – ASCII lower case)
    ("grey_cement_sales", r"gri cimento"),
    ("white_cement_sales", r"beyaz cimento"),
    ("cac_sales", r"kalsiyum aluminatli cimento"),
    ("clinker_sales", r"klinker"),
    ("ready_mix_sales", r"hazir beton"),
    ("cement_sales_total", r"(?<![a-z] )(?<!gri )(?<!beyaz )(?<!aluminatli )\bcimento"),
]
UNIT_RE = r"\(?(milyon ton|bin ton|ton|milyon m3|m3)\)?"
SECTOR_ITEMS = [  # (item, key phrase regex)
    ("sector_cement_clinker_exports", r"cimento ve klinker ihracat"),
    ("sector_cement_exports", r"(?<!ve )cimento ihracat"),
    ("sector_clinker_exports", r"(?<!cimento ve )klinker ihracat"),
    ("sector_cement_production", r"cimento uretim(?:i|inde)"),
    ("sector_clinker_production", r"klinker uretim(?:i|inde)"),
    ("sector_domestic_consumption", r"(?:yurt ?ici|ic) (?:pazar |toplam )?(?:cimento )?(?:tuketim|satis|talep|talebi)(?:i|lari|inda)?\b"),
]
OTHER_MEASURES = r"uretim|ihracat|tuketim|satis|talep|kapasite"
SECTOR_CUE = r"tcmb|turk ?cimento|turkiye|ulkemiz|sektor|yurt ?ici|ihracatcilar birligi|oaib|ccsib"
FOREIGN_CUE = r"dunya|kuresel|abd\b|avrupa|cin\b|milyar|amerika|ispanya"
PERIODS = [(r"ilk on bir ay|ilk 11 ay|ocak ?- ?kasim", "11M"), (r"ilk on ay|ilk 10 ay|ocak ?- ?ekim", "10M"),
           (r"ilk dokuz ay|ilk 9 ay|ocak ?- ?eylul", "9M"), (r"ocak ?- ?aralik", "FY")]
# comparison year: forms with dative/genitive suffixes ("2023 yılına", "2017 yılının aynı dönemine", "2022'ye göre")
COMPARE_YEAR = r"20[12]\d ?(?:yilina|yilinin ayni donemine|ye gore|ya gore|e gore|a gore)"
# year stated explicitly together with the period ("2019 yılının ilk on bir ayında", "ocak-kasım 2017 döneminde")
EXPLICIT_YEAR = (r"(20[12]\d) ?yili(?:nin)? (?:ilk on bir ay|ilk on ay|ilk dokuz ay|ilk 11 ay|ocak ?- ?(?:kasim|ekim|eylul|aralik))"
                 r"|ocak ?- ?(?:kasim|ekim|eylul|aralik) (20[12]\d)")
SECTOR_MIN = {"sector_cement_production": 20, "sector_clinker_production": 20, "sector_domestic_consumption": 20}  # million tons
# (report year, page, item, value) -> (year, period, evidence). Used where the automatic assignment picks the wrong year in the text.
VERIFIED_SECTOR = {
    (2020, 70, "sector_cement_exports", 14.8): (2020, "11M", "'TürkÇimento üyesi fabrikalar dikkate alındığında 2020 … yılının ilk on bir ayında çimento ihracatı' (cement exports of TürkÇimento member plants in the first eleven months of 2020)"),
    (2020, 70, "sector_clinker_exports", 12.5): (2020, "11M", "same sentence: '… klinker ihracatı ise %16 artarak 12,5 milyon ton' (clinker exports up 16% to 12.5 million tons)"),
    (2019, 3, "sector_cement_clinker_exports", 23.0): (2019, "FY (assumed)", "2019 report, p.4 same phrase: 'ülkemiz … 2019 … yılında 23 milyon ton çimento ve klinker ihracatı' (23 million tons of cement and clinker exports in 2019)"),
    (2019, 4, "sector_cement_clinker_exports", 23.0): (2019, "FY (assumed)", "'ülkemiz … 2019 … yılında 23 milyon ton çimento ve klinker ihracatı' (23 million tons of cement and clinker exports in 2019)"),
    (2019, 57, "sector_cement_exports", 10.3): (2019, "11M", "paragraph starts with 'TÇMB verilerine göre 2019 yılının ilk on bir ayında Türkiye çimento üretimi …' (according to TÇMB, Turkey's cement production in the first eleven months of 2019)"),
    (2019, 57, "sector_clinker_exports", 10.8): (2019, "11M", "same sentence: '… klinker ihracatı ise %108 artarak 10,8 milyon ton' (clinker exports up 108% to 10.8 million tons)"),
    (2021, 69, "sector_domestic_consumption", 55.6): (2020, "FY (assumed)", "same paragraph '2020 yılında üretilen çimentonun …' (of the cement produced in 2020); the 2020 report gives domestic sales growth of ~23%"),
}
SOURCES = [(r"tcmb|cimento mustahsilleri", "TÇMB"), (r"turk ?cimento", "TürkÇimento"),
           (r"ccsib|cimento, cam, seramik", "ÇCSİB"), (r"oaib|orta anadolu ihracatcilar", "OAİB")]


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(ASCII_MAP).lower()).strip()


def decimals(tok: str) -> int:
    m = re.fullmatch(r"\d+[,.](\d{1,2})", tok.strip("*"))
    return len(m.group(1)) if m else 0


def parse_num(tok: str):
    t = tok.strip("*")
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", t):
        return float(t.replace(".", "")), ""
    if re.fullmatch(r"\d+,\d+", t):
        return float(t.replace(",", ".")), ""
    if re.fullmatch(r"\d+", t):
        return float(t), ""
    if re.fullmatch(r"\d+\.\d{1,2}", t):
        return float(t), "decimal separator is a dot (ambiguous format)"
    return None, "malformed number"


def read_numbers(rest: str):
    """Number sequence after the label/unit: [(value, inline multiplier, note)]; stops at the first non-number token."""
    out, toks, i = [], rest.split(), 0
    while i < len(toks):
        t = toks[i]
        if t == "-":
            out.append((None, 1.0, "'-' in report", 0)); i += 1; continue
        v, n = parse_num(t)
        if v is None:
            break
        mult = 1.0
        if i + 1 < len(toks) and toks[i + 1] in INLINE_MULT:
            mult = INLINE_MULT[toks[i + 1]]; i += 1
        out.append((v, mult, n, decimals(t))); i += 1
    return out


def header_years(lines_norm, upto):
    """First consecutive year pair above the line ("2015 2016", "Birim 2023 2024 Dış Güvence")."""
    for j in range(upto, -1, -1):
        for a, b in re.findall(r"\b(20[12]\d) (20[12]\d)\b", lines_norm[j]):
            if int(b) == int(a) + 1:
                return [int(a), int(b)]
    return None


def cand(item, year, year_unc, value, unit, ry, rel, page, raw, note, kind, period="FY", source="", dec=0, mult=1.0):
    # half of the last displayed digit (before unit conversion, in the report's unit)
    precision = 0.5 * 10 ** (-dec) * mult if value is not None else None
    return {"item": item, "kind": kind, "year": year, "year_uncertain": year_unc, "value": value, "unit": unit,
            "precision": precision,
            "period": period, "source_org": source, "report_year": ry, "source_file": rel, "source_page": page,
            "raw_text": raw[:300], "note": note}


# ---------------------------------------------------------------- Çimsa sales

def company_rows(ry, rel, pno, lines):
    ln = [norm(l) for l in lines]
    rows = []
    # Format A (2016–2018)
    for i, l in enumerate(ln):
        m = re.match(r"^(gri cimento|beyaz cimento|kalsiyum aluminatli cimento|klinker|hazir beton) satisi \((ton|milyon ton|m3)\)\s*(.*)$", l)
        if m:
            item = {"gri cimento": "grey_cement_sales", "beyaz cimento": "white_cement_sales",
                    "kalsiyum aluminatli cimento": "cac_sales", "klinker": "clinker_sales",
                    "hazir beton": "ready_mix_sales"}[m.group(1)]
            rows += make_company(item, m.group(2), read_numbers(m.group(3)), header_years(ln, i), ry, rel, pno, lines[i],
                                 "performance table ('… Satışı' (sales) line)")
    # Format B (2019–2025)
    for h, l in enumerate(ln):
        if not re.search(r"toplam satis(?: miktari)?\b", l):
            continue
        stop = next((k for k in range(h + 1, min(h + 14, len(ln))) if re.search(r"ekonomik deger|net satislar", ln[k])), min(h + 12, len(ln)))
        years = header_years(ln, h)
        for k in range(h, stop):
            seg = ln[k].split("toplam satis", 1)[1] if k == h else ln[k]
            for item, lab in PRODUCTS:
                m = re.search(lab + r"\s*(\*|\d(?=\s))?\s*" + UNIT_RE + r"\s+(.*)$", seg)
                if not m:
                    continue
                if item == "cement_sales_total" and re.search(r"(gri|beyaz|aluminatli)\s*$", seg[:m.start()]):
                    continue
                marker = m.group(1) or ""
                note = "performance table ('Toplam Satış' (total sales) section)"
                if item == "cement_sales_total":
                    note += f"; total cement line (label marker '{marker}')"
                rows += make_company(item, m.group(2), read_numbers(m.group(3)), years, ry, rel, pno, lines[k], note)
    return rows


def make_company(item, unit, nums, years, ry, rel, pno, raw, note):
    rows = []
    vals = [n for n in nums if True][:2]
    if not vals:
        return rows
    if years and len(vals) == 2:
        pairs = list(zip(years, vals)); unc = False
    else:
        pairs = [(None, v) for v in vals]; unc = True
        note += "; number count does not match the year header or no header; no year assigned"
    for y, (v, mult, n, dec) in pairs:
        rows.append(cand(item, y, unc, v * mult if v is not None else None, unit, ry, rel, pno, raw,
                         "; ".join(filter(None, [note, n, "inline multiplier '" + ("bin" if mult == 1e3 else "milyon") + "'" if mult != 1.0 else ""])),
                         "company", dec=dec, mult=mult))
    return rows


def company_export_narrative(ry, rel, pno, text_norm, raw_text):
    rows = []
    for m in re.finditer(r"cimsa ?n?in (20[12]\d) ihracati (\d+(?:,\d+)?) milyon ton", text_norm):
        v, _ = parse_num(m.group(2))
        rows.append(cand("export_sales_total", int(m.group(1)), False, v, "milyon ton", ry, rel, pno, m.group(0),
                         "narrative text (not a table); no product breakdown", "company"))
    return rows


# ---------------------------------------------------------------- sector

def sector_rows(ry, rel, page_texts):
    rows = []
    for pno, txt in page_texts:
        for item, pat in SECTOR_ITEMS:
            for m in re.finditer(pat, txt):
                before = txt[max(0, m.start() - 700):m.start()]
                after = txt[m.end():m.end() + 170]
                # number after the key phrase (with no other indicator in between) or right before it ("67 milyon ton çimento üretimi")
                n = re.search(r"(\d{1,3}(?:[.,]\d{1,3})?)\s?(?:milyon ton|mt\b|milyon tonluk)", after)
                pos = "after"
                if n and re.search(OTHER_MEASURES, after[:n.start()]):
                    n = None
                if not n:
                    nb = re.search(r"(\d{1,3}(?:[.,]\d{1,3})?)\s?milyon ton\s*$", before[-30:])
                    if not nb:
                        continue
                    n, pos = nb, "before"
                gap = after[:n.start()] if pos == "after" else ""
                if re.search(FOREIGN_CUE, before[-90:] + gap) or \
                        not re.search(SECTOR_CUE, before[-320:] + txt[m.start():m.end()] + gap):
                    continue
                if re.search(r"cimsa|uretimimiz|satisimiz|fabrikamiz", before[-120:] + gap):
                    continue  # the company's own figures
                ctx = before + txt[m.start():m.end()] + after
                v, vn = parse_num(n.group(1))
                dec = decimals(n.group(1))
                if v is not None and v < SECTOR_MIN.get(item, 1):
                    continue  # not plausible for the Turkish sector (may be a company figure)
                look = before[-450:] + gap
                exp_m = list(re.finditer(EXPLICIT_YEAR, before + gap))
                # explicit period phrase (may be further away; other text can intervene in two-column layouts)
                period = next((p for rx, p in PERIODS if re.search(rx, exp_m[-1].group(0))), None) if exp_m else \
                    next((p for rx, p in PERIODS if re.search(rx, look)), None)
                # comparison years ("2022 yılına göre", "2017 yılının aynı dönemine") are not used for year assignment
                exp = [x.group(1) or x.group(2) for x in exp_m]
                yrs = re.findall(r"(20[12]\d) ?(?:yili|yilinda|yil)\b", re.sub(COMPARE_YEAR, " ", look))
                yrs = [y for y in yrs if int(y) >= 2015]  # old years (e.g. plant text "2012 yılı nisan ayında") cannot be the sector year
                if exp:
                    year, unc = int(exp[-1]), False
                elif yrs:
                    year, unc = int(yrs[-1]), False
                else:
                    year, unc = ry, True
                if period is None:
                    period = "FY (assumed)"
                src = ", ".join(s for rx, s in SOURCES if re.search(rx, before[-450:] + gap))
                notes = [vn] if vn else []
                if unc:
                    notes.append("no year in the text; report year assumed")
                if "turkcimento uyeler" in ctx or "uye fabrika" in ctx:
                    notes.append("scope: TürkÇimento/TÇMB member plants")
                if pos == "before":
                    notes.append("number before the key phrase")
                start = max(0, m.start() - 120)
                rows.append(cand(item, year, unc, v, "milyon ton", ry, rel, pno, txt[start:m.end() + 140],
                                 "; ".join(notes), "sector", period=period, source=src, dec=dec))
    return rows


# ---------------------------------------------------------------- candidate extraction

def extract_candidates():
    rows = []
    for ry in YEARS:
        path = DATA_DIR / f"{ry}.pdf"
        rel = path.relative_to(ROOT).as_posix()
        page_texts = []
        with pdfplumber.open(path) as pdf:
            for pno, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                lines = text.split("\n")
                rows += company_rows(ry, rel, pno, lines)
                tn = norm(text)
                rows += company_export_narrative(ry, rel, pno, tn, text)
                page_texts.append((pno, tn))
        rows += sector_rows(ry, rel, page_texts)
        print(f"  {ry}: {sum(1 for r in rows if r['report_year'] == ry)} candidates")
    df = pd.DataFrame(rows)
    # Manually verified year/period assignments (the two-column layout separates the year from the sentence; evidence in the raw text)
    for (ry, page, item, value), (year, period, why) in VERIFIED_SECTOR.items():
        hit = (df["report_year"] == ry) & (df["source_page"] == page) & (df["item"] == item) & (df["value"] == value)
        df.loc[hit, ["year", "period", "year_uncertain"]] = [year, period, False]
        df.loc[hit, "note"] = df.loc[hit, "note"].fillna("").map(lambda n: "; ".join(filter(None, [n, f"year/period verified manually: {why}"])))
    df = df.drop_duplicates(subset=["item", "year", "value", "unit", "report_year", "period"])
    return df.sort_values(["kind", "item", "year", "report_year", "source_page"]).reset_index(drop=True)


# ---------------------------------------------------------------- standardization and annual table

def standardize(r):
    v, unit, note, quality = r["value"], r["unit"], [], "ok"
    if pd.isna(v):
        return None, None, note, "missing"
    target = "m3" if "m3" in unit else "ton"
    f = UNIT_FACTOR[unit]
    if unit == "milyon ton" and v >= 1000:  # "Klinker Satışı (milyon ton) 709.872": value is ton-sized
        f = 1.0
        note.append(f"report states unit '{unit}' but the value is ton-sized; taken as tons")
        quality = "unit_corrected"
    if "inline multiplier" in str(r["note"]):
        f = 1.0  # multiplier already applied to the value
    return v * f, target, note, quality


SCOPE_NOTES = {
    ("cement_sales_total", 2024): "clinker included (report footnote); no grey/white/CAC split",
    ("cement_sales_total", 2025): "clinker included (report footnote); the same page also has the footnote 'Mannok Holding DAC hariçtir' (excluding Mannok Holding DAC), unclear which line it belongs to",
}


def build_annual(c: pd.DataFrame) -> pd.DataFrame:
    c = c[c["year"].notna() & ~c["year_uncertain"].astype(bool) | (c["kind"] == "sector")].copy()
    std = c.apply(standardize, axis=1, result_type="expand")
    c["value_std"], c["unit_std"], c["std_note"], c["quality"] = std[0], std[1], std[2], std[3]
    c["precision_std"] = [p * (UNIT_FACTOR[u] if q != "unit_corrected" and "inline multiplier" not in str(nt) else 1.0)
                          if pd.notna(p) else 0.0
                          for p, u, q, nt in zip(c["precision"], c["unit"], c["quality"], c["note"])]
    c = c[c["value_std"].notna() & c["year"].notna()]
    c["year"] = c["year"].astype(int)
    out = []
    for (item, year), g in c.groupby(["item", "year"]):
        if year not in YEARS:
            continue
        own = g[g["report_year"] == year]
        later = g[g["report_year"] > year]
        if g["kind"].iloc[0] == "sector":
            rank = {"FY": 0, "FY (assumed)": 1, "11M": 2, "10M": 3, "9M": 4}
            own = own.assign(_r=own["period"].map(rank).fillna(9)).sort_values(["_r", "source_page"])
            later = later.assign(_r=later["period"].map(rank).fillna(9)).sort_values(["report_year", "_r"])
        else:
            own = own.sort_values("source_page")
            later = later.sort_values(["report_year", "source_page"])
        primary, restated = None, False
        if len(own):
            primary = own.iloc[0]
            conflicts = own[(own["value_std"] - primary["value_std"]).abs() >
                            pd.concat([own["precision_std"], pd.Series(primary["precision_std"], index=own.index)], axis=1).max(axis=1)
                            + primary["value_std"] * RESTATE_TOL]
        elif len(later):
            primary, restated, conflicts = later.iloc[0], True, later.iloc[0:0]
        else:
            continue
        def row(r, rest, method):
            notes = list(r["std_note"]) + [n for n in [r["note"], SCOPE_NOTES.get((item, year), "")] if n]
            q = r["quality"]
            if r["kind"] == "sector" and r["period"] not in ("FY", "FY (assumed)"):
                q = "partial_year"
                notes.append(f"partial year ({r['period']}); not comparable with a full year")
            if r["year_uncertain"]:
                q = "year_uncertain" if q == "ok" else q
            return {"item": item, "kind": r["kind"], "year": year, "period": r["period"], "value": r["value_std"],
                    "unit": r["unit_std"], "restated": rest, "report_year": int(r["report_year"]),
                    "source_file": r["source_file"], "source_page": int(r["source_page"]),
                    "original_value": r["value"], "original_unit": r["unit"], "source_org": r["source_org"],
                    "method": method, "quality": q, "note": "; ".join(notes)}
        out.append(row(primary, restated, "own report" if not restated else
                       "no value in own report; value from the following report used"))
        if len(conflicts):
            out[-1]["note"] += f"; the same report also has different value(s): {sorted(set(conflicts['value_std']))}"
            out[-1]["quality"] = "suspect" if out[-1]["quality"] == "ok" else out[-1]["quality"]
        # different values in later reports
        if not restated:
            for _, r in later.drop_duplicates("report_year").iterrows():
                tol = max(r["precision_std"], primary["precision_std"]) + primary["value_std"] * RESTATE_TOL
                if abs(r["value_std"] - primary["value_std"]) > tol or \
                        (r["kind"] == "sector" and r["period"] != primary["period"]):
                    out.append(row(r, True, f"different value in the {int(r['report_year'])} report"))
    t = pd.DataFrame(out)
    return t.sort_values(["kind", "item", "year", "restated", "report_year"]).reset_index(drop=True)


# ---------------------------------------------------------------- checks

def build_checks(t: pd.DataFrame) -> pd.DataFrame:
    rows = []
    prim = t[~t["restated"] | (t["method"].str.startswith("no value in own report"))]
    for item, g in prim.groupby("item"):
        g = g.sort_values("year")
        for (_, a), (_, b) in zip(g.iloc[:-1].iterrows(), g.iloc[1:].iterrows()):
            if b["year"] != a["year"] + 1 or not a["value"]:
                continue
            if a["kind"] == "sector" and a["period"] != b["period"]:
                continue
            ch = (b["value"] / a["value"] - 1) * 100
            if abs(ch) > YOY_FLAG:
                rows.append({"check": "yoy_change_gt_30pct", "item": item, "year": b["year"], "value_a": a["value"],
                             "value_b": b["value"], "diff_pct": round(ch, 1),
                             "detail": f"{a['year']} → {b['year']}; source: {a['report_year']} / {b['report_year']} report"})
    # production vs sales
    esg = pd.read_csv(ESG_IN)
    esg = esg[~esg["restated"]]
    pairs = [("grey_cement_sales", "grey_cement_production"), ("white_cement_sales", "white_cement_production"),
             ("cac_sales", "cac_production"), ("ready_mix_sales", "ready_mix_production"),
             ("cement_sales_total", "cement_production_total")]
    for s_item, p_item in pairs:
        for _, s in prim[prim["item"] == s_item].iterrows():
            p = esg[(esg["item"] == p_item) & (esg["year"] == s["year"])]
            if not len(p):
                continue
            pv = p["value"].iloc[0]
            gap = (s["value"] / pv - 1) * 100
            status = "warning" if abs(gap) > GAP_FLAG else "passed"
            rows.append({"check": "sales_vs_production", "item": s_item, "year": s["year"], "value_a": pv,
                         "value_b": s["value"], "diff_pct": round(gap, 1),
                         "detail": f"{status}: sales / production − 1 (threshold ±{GAP_FLAG:.0f}%); production from esg_kpis_annual {p_item} "
                                   f"({p['note'].iloc[0] if isinstance(p['note'].iloc[0], str) else ''})"})
    # clinker sales / clinker production (info)
    for _, s in prim[prim["item"] == "clinker_sales"].iterrows():
        p = esg[(esg["item"] == "clinker_production") & (esg["year"] == s["year"])]
        if len(p):
            rows.append({"check": "clinker_sales_share_of_production", "item": "clinker_sales", "year": s["year"],
                         "value_a": p["value"].iloc[0], "value_b": s["value"],
                         "diff_pct": round(s["value"] / p["value"].iloc[0] * 100, 1),
                         "detail": "info: clinker sold / clinker produced (%); most clinker is used in cement production"})
    # quality flags
    for _, r in t[t["quality"] != "ok"].iterrows():
        rows.append({"check": f"quality_{r['quality']}", "item": r["item"], "year": r["year"], "value_a": None,
                     "value_b": r["value"], "diff_pct": None, "detail": f"{r['report_year']} report: {r['note']}"})
    return pd.DataFrame(rows)


def main():
    c = extract_candidates()
    CAND_OUT.parent.mkdir(parents=True, exist_ok=True)
    c.to_csv(CAND_OUT, index=False, encoding="utf-8")
    t = build_annual(c)
    t.to_csv(TABLE_OUT, index=False, encoding="utf-8")
    ck = build_checks(t)
    CHECK_OUT.parent.mkdir(parents=True, exist_ok=True)
    ck.to_csv(CHECK_OUT, index=False, encoding="utf-8")
    print(f"Candidates: {CAND_OUT.relative_to(ROOT)} ({len(c)} rows)")
    print(f"Table     : {TABLE_OUT.relative_to(ROOT)} ({len(t)} rows)")
    print(f"Check     : {CHECK_OUT.relative_to(ROOT)} ({len(ck)} rows)")


if __name__ == "__main__":
    main()
