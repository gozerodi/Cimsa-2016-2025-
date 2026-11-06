"""
02 – Extracts physical energy and production indicators from the integrated annual reports.

Source : Data/IntegratedReports/2016.pdf … 2025.pdf
Outputs:
  output/tables/esg_kpis_candidates.csv  (all candidate values; no single value is selected)
  output/checks/esg_kpis_conflicts.csv   (different values for the same indicator/year/scope/unit)
  output/checks/esg_report_boundaries.csv (scope/boundary sentences of the reports, quoted verbatim)

Method:
- Each page is searched for lines containing the indicator name; the numbers right after the name
  (after the unit and parenthesised qualifiers) are read. Narrative sentences (number before the name,
  or other words in between) are skipped.
- The year is assigned in order from the year header on the same page (e.g. "Birim 2021 2022").
  * If the number count does not match the header, year is left blank and year_uncertain = True.
  * For single-number lines (SASB index, summary boxes) year = report year is assumed, year_uncertain = True.
- Values are not unit-converted; the Turkish number format (thousands dot, decimal comma) is parsed.
  For malformed numbers (e.g. with an attached footnote digit "18.1143", "%655") value is left blank.
- In production tables, lines under the "Toplam Satış Miktarı" (total sales volume) section (sales data) are skipped.
"""
from collections import Counter
from pathlib import Path
import re

import pandas as pd
import pdfplumber

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "Data" / "IntegratedReports"
TABLE_OUT = ROOT / "output" / "tables" / "esg_kpis_candidates.csv"
CHECK_OUT = ROOT / "output" / "checks" / "esg_kpis_conflicts.csv"
BOUNDARY_OUT = ROOT / "output" / "checks" / "esg_report_boundaries.csv"
YEARS = range(2016, 2026)

ASCII_MAP = str.maketrans("çğıöşüÇĞİÖŞÜâÂ", "cgiosuCGIOSUaA")

PROD_UNIT = r"(?= \(?(?:milyon ton|bin ton|ton|milyon m3|m3)\)?(?: |$))"

# (item, regex searched in the normalized text, context type)
# Order matters: the first matching pattern takes a given position.
# Context type: None | "prod" (total production/sales section) | "raw" (raw material section) | "afr" (alt. fuel heading)
ITEM_PATTERNS = [
    ("total_energy_consumption", r"klinker uretimindeki toplam enerji tuketimi", None),
    ("total_energy_consumption", r"toplam enerji tuketimi", None),
    ("thermal_energy_conventional", r"konvansiyonel enerji tuketimi", None),
    ("thermal_energy_alternative", r"alternatif enerji tuketimi", None),
    ("electricity_consumption", r"elektrik tuketimi", None),
    ("electricity_consumption", r"^elektrik(?= mwh)", None),
    ("alt_fuel_rate", r"alternatif yakit kullanim orani(?: miktari)?\b", None),
    ("alt_fuel_rate", r"^\*?(?:gri|beyaz) cimento(?= %)", "afr"),
    ("renewable_electricity_share", r"yenilenebilir elektrik enerjisi (?:kullanim orani|tuketimi)\b", None),
    ("renewable_electricity_share", r"yenilenebilir enerji (?:tuketimi ve uretimi|yuzdesi)\b", None),
    ("self_generated_renewable_electricity", r"atik isidan (?:elektrik enerjisi uretim miktari|enerji kazanimi)", None),
    ("self_generated_renewable_electricity", r"gunes enerjisi(?: uretim miktari)?(?= *(?:mwh|\d))", None),
    ("self_generated_renewable_electricity", r"toplam yenilenebilir enerji uretimi", None),
    ("specific_heat_consumption", r"spesifik isil tuketim", None),
    ("specific_electricity_consumption", r"spesifik elektrik tuketimi|enerji yogunlugu \(gri klinker\)", None),
    ("clinker_production", r"klinker uretimi\b", None),
    ("clinker_production", r"uretilen klinker", "raw"),
    ("grey_cement_production", r"gri cimento uretim(?:i)?\b", None),
    ("white_cement_production", r"beyaz cimento uretim(?:i)?\b", None),
    ("cac_production", r"kalsiyum aluminat(?:li)? cimento(?:su)? uretim(?:i)?\b", None),
    ("ready_mix_production", r"hazir beton uretim(?:i)?\b", None),
    # Short labels in the "Toplam Üretim Miktarı" (total production) table
    ("grey_cement_production", r"\bgri cimento" + PROD_UNIT, "prod"),
    ("white_cement_production", r"\bbeyaz cimento" + PROD_UNIT, "prod"),
    ("cac_production", r"\bkalsiyum aluminat(?:li)? cimento" + PROD_UNIT, "prod"),
    ("clinker_production", r"(?<!uretilen )\bklinker" + PROD_UNIT, "prod"),
    ("ready_mix_production", r"\bhazir beton" + PROD_UNIT, "prod"),
    ("cement_production_total", r"(?<!kullanilan )(?<!uretilen )(?<!gri )(?<!beyaz )\bcimento[*1]?" + PROD_UNIT, "prod"),
    # Renewable energy consumption (MWh)
    ("renewable_energy_consumption", r"yenilenebilir enerji kaynaklarindan saglanan enerji tuketimi", None),
    ("renewable_energy_consumption", r"yenilenebilir enerji kullanimi", None),
]

# Label of the I-REC certified renewable electricity line (numbers may be on the next line)
IREC_LABEL = re.compile(r"sertifikasi standardina (?:\(i-rec\) )?uygun tedarik")

# Report scope / boundary sentences: report year -> [(regex searched in the text flow, note)]
# Anchor phrases were identified by reading the reports manually; the sentence text is quoted verbatim from the PDF.
S = r"[^.]{0,400}?"  # short gap within a sentence (no full stop)
BOUNDARY_ANCHORS = {
    2016: [(r"Çimsa iştiraki \(%51\) olan Afyon Çimento.{0,250}?dahil değildir\.", "About the report"),
           (r"Çimsa’nın hedefi, Afyon Çimento’nun" + S + r"\.", "About the report")],
    2017: [(r"Çimsa iştiraki \(%51\) olan Afyon Çimento.{0,250}?dahil değildir\.", "About the report")],
    2018: [(r"Çimsa’nın iştiraki \(%51\) olan Afyon Çimento.{0,250}?dahildir\.", "About the report"),
           (r"Performans tabloları, Afyon Çimento verileri 2017" + S + r"\.", "Performance table footnote")],
    2019: [(r"Entegre Faaliyet Raporu’nda yer alan tüm bilgiler, aksi belirtilmediği sürece" + S + r"\.",
            "About the report; plant/country scope not stated separately")],
    2020: [(r"Entegre Faaliyet Raporu’nda yer alan tüm bilgiler, aksi belirtilmediği sürece" + S + r"\.",
            "About the report; plant/country scope not stated separately")],
    2021: [(r"Rapor, Türkiye sınırları içerisinde" + S + r"\.", "About the report"),
           (r"Bu kılavuzda yer alan bilgiler 31 Aralık 2021.{0,500}?hariç tutmaktadır\.", "Reporting guideline")],
    2022: [(r"Raporumuz, Türkiye sınırları içerisinde" + S + r"\.", "About the report"),
           (r"Bu kılavuzda yer alan bilgiler 31 Aralık 2022.{0,600}?Kıbrıs Transfer Merkezi operasyonlarını kapsamaktadır\.",
            "Reporting guideline"),
           (r"Sera gazı emisyonlarını raporlama çerçevemizi genişletme" + S + r"\.", "Performance table footnote"),
           (r"2022'deki kapsamın genişletilmesinden dolayı 2021 verileri revize edilmiştir\.",
            "Performance table footnote (social indicators)")],
    2023: [(r"Raporumuz, Türkiye’deki 3 entegre çimento fabrikası.{0,500}?(?:içerir|içermektedir|kapsar|kapsamaktadır)\.",
            "About the report"),
           (r"Bu (?:kılavuzda|prensipte) yer alan bilgiler 31 Aralık 2023.{0,800}?Sabancı Global Technology Center’ın operasyonlarını kapsamaktadır\.",
            "Reporting guideline"),
           (r"2022 yılı sadece Çimsa verilerini kapsar\.", "Performance table footnote; which line it belongs to is not clear from the text"),
           (r"Sadece Buñol Fabrikası’nın üretimini kapsar", "Performance table footnote (the white production alternative fuel rate line carries marker 1)"),
           (r"Veri 2022 ve 2023 yılları için sadece Türkiye lokasyonları ve Kıbrıs Terminali’ni kapsamaktadır\.",
            "Performance table footnote (social indicators)")],
    2024: [(r"Türkiye’de faaliyet gösteren 4 entegre çimento fabrikası" + S + r"\.", "About the report"),
           (r"İrlanda’da kurulu Mannok.{0,600}?dahil edilmiştir\.", "About the report"),
           (r"Bu kapsamda yalnızca “çalışan Demografisi”" + S + r"\.", "Performance table footnote"),
           (r"Diğer tüm performans göstergelerinde ise Mannok" + S + r"\.", "Performance table footnote"),
           (r"Şirket’in Ekim 2024’te grup bünyesine dahil ettiği Mannok" + S + r"\.", "Reporting guideline"),
           (r"\*Mannok operasyonları grafiğe dahil değildir\.", "Chart footnote"),
           (r"\*Klinker dahildir\.", "Performance table footnote (production/sales table)"),
           (r"\*Beyaz Çimento alternatif yakıt kullanım oranı Bunol fabrikasına aittir\.", "Performance table footnote")],
    2025: [(r"Bu kapsamda, söz konusu faaliyet alanlarına" + S + r"\.", "About the report"),
           (r"Bu kapsamda raporumuz, Türkiye’deki 4 üretim tesisi[^.]{0,900}?\.", "About the report"),
           (r"RAPORLAYAN İŞLETME SINIRLARI VE ÖLÇÜM YAKLAŞIMI.{0,400}?içermektedir\.", "TSRS climate report section"),
           (r"1Mannok Holding DAC hariçtir\.", "Performance table footnote; which line it belongs to is not clear from the text"),
           (r"1Klinker dahildir\.", "Performance table footnote (production/sales table)"),
           (r"2Mannok Holdings? DAC hariçtir\.", "Performance table footnote"),
           (r"1Mannok Holding DAC ve Bunol Fabrikası hariçtir\.", "Performance table footnote"),
           (r"(?:[\d-]+ ?)?2024 değeri Mannok Holding DAC dahil edilerek revize edilmiştir\.", "Target/performance table footnote"),
           (r"(?:\d ?)?Mannok Holding DAC alımı ile beraber hedef güncellenmiş" + S + r"\.", "Target table footnote"),
           (r"2?Mannok'un 2024-2025 yıllarında da 12 aylık verileri dahildir\.", "Chart/table footnote"),
           (r"1Beyaz Çimento alternatif yakıt kullanım oranı Bunol fabrikasına aittir\.", "Performance table footnote")],
}

# Section headings (tracked separately for the left and right column in two-column layouts)
PROD_HEADINGS = [(r"toplam uretim\b(?! maliyet)", "total production"), (r"toplam satis\b", "SALES")]
RAW_HEADINGS = [(r"gri cimento ham ?madde", "grey cement"), (r"beyaz cimento ham ?madde", "white cement"),
                (r"kalsiyum aluminat cimentosu (?:\(ton\)|ham ?madde)", "CAC"),
                (r"(?<!gri )(?<!beyaz )\bcimento ham ?madde", "cement (total)"), (r"hazir beton ham ?madde", "ready-mix concrete")]

# Words allowed between the label and the first number (unit / qualifier). Any other word means the line is narrative.
GAP_WORDS = {
    "mwh", "gwh", "kwh", "tj", "gj", "mj", "mj/ton", "kwh/ton", "klinker", "ton", "m3", "%", "*", "**",
    "mwh/yil", "mwh/yr", "tj/yil", "mw/yil", "gri", "beyaz", "uretim", "miktari", "milyon", "bin",
    ":", "-", "·", ".",
}
UNIT_RE = re.compile(r"(mj/ton klinker|kwh/ton|mwh/yil|mwh/yr|tj/yil|milyon ton|bin ton|milyon m3|mwh|gwh|kwh|tj|gj|m3|ton|%)")
AFTER_UNIT_RE = re.compile(r"^(milyon ton|bin ton|milyon m3|milyon mwh|bin mwh|mwh|ton|m3)\b")
NUM_TOKEN = re.compile(r"^%?(?:\d[\d.,]*|-)%?$")
YEAR_SEQ = re.compile(r"\b(20[12]\d)((?:\s+20[12]\d)+)\b")


def normalize(text: str) -> str:
    return text.translate(ASCII_MAP).lower()


def parse_value(tok: str, unit: str):
    """Turkish-format number -> (float or None, note). None + explanation if malformed."""
    t = tok.strip("%")
    value = None
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", t):
        value = float(t.replace(".", "").replace(",", "."))
    elif re.fullmatch(r"[1-9]\d*(?:,\d+)?|0(?:,\d+)?", t):
        value = float(t.replace(",", "."))
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})*\.\d{4}", t):
        return None, f"malformed ('{tok}'): a footnote digit may be attached at the end"
    elif re.fullmatch(r"\d+\.\d{1,2}", t):
        return None, f"ambiguous format ('{tok}'): unclear whether the dot is a decimal point or a thousands separator"
    else:
        return None, f"malformed ('{tok}')"
    if unit == "%" and value > 100:
        return None, f"percentage above 100 ('{tok}'): a footnote digit may be attached"
    return value, ""


def page_header_years(norm_lines):
    """Most frequent consecutive year sequence on the page (e.g. [2021, 2022]). None if absent."""
    seqs = []
    for line in norm_lines:
        for m in YEAR_SEQ.finditer(line):
            ys = [int(m.group(1))] + [int(y) for y in m.group(2).split()]
            half = len(ys) // 2  # "2018 2019 2018 2019" (two-column layout) -> [2018, 2019]
            if len(ys) % 2 == 0 and ys[:half] == ys[half:]:
                ys = ys[:half]
            if all(b - a == 1 for a, b in zip(ys, ys[1:])):
                seqs.append(tuple(ys))
    if not seqs:
        return None
    return list(Counter(seqs).most_common(1)[0][0])


def scope_of(item, pre, label, gap):
    ctx = f"{pre} {label} {gap}"
    parts = []
    if "gri+beyaz" in ctx.replace(" ", ""):
        parts.append("grey+white")
    elif re.search(r"\bgri\b", ctx) and item != "grey_cement_production":
        parts.append("grey")
    elif re.search(r"\bbeyaz\b", ctx) and item != "white_cement_production":
        parts.append("white")
    for key, text in [("pazar bazli", "market-based"), ("lokasyon bazli", "location-based"),
                      ("klinker uretimindeki", "clinker production"), ("atik isi", "waste heat (WHR)"),
                      ("gunes", "solar (PV)"), ("toplam yenilenebilir enerji uretimi", "total (WHR+PV)")]:
        if key in ctx:
            parts.append(text)
    return parts


def parse_after_label(norm, end):
    """After the label: (gap text, number tokens, text after the numbers) or None (narrative)."""
    tokens = norm[end:].split()
    gap_tokens, depth, k = [], 0, 0
    while k < len(tokens):
        tok = tokens[k]
        if depth > 0 or tok.startswith("("):
            gap_tokens.append(tok)
            depth += tok.count("(") - tok.count(")")
        elif NUM_TOKEN.match(tok):
            break
        elif tok.rstrip("*") in GAP_WORDS or tok in GAP_WORDS:
            gap_tokens.append(tok)
        else:
            return None
        k += 1
    if len(gap_tokens) > 12:
        return None
    nums = []
    while k < len(tokens) and NUM_TOKEN.match(tokens[k]):
        nums.append(tokens[k])
        k += 1
    after = tokens[k:]
    # "2,48 milyon m3 2,9 milyon m3" format: number + repeated unit
    am = AFTER_UNIT_RE.match(" ".join(after))
    if len(nums) == 1 and am:
        unit_len = len(am.group(1).split())
        nxt = after[unit_len:]
        if len(nxt) > unit_len and NUM_TOKEN.match(nxt[0]) and " ".join(nxt[1:1 + unit_len]) == am.group(1):
            nums.append(nxt[0])
            after = nxt[1 + unit_len:]
    return " ".join(gap_tokens), nums, " ".join(after)


def find_headings(norm, headings):
    """Returns the headings in the line as (position, value)."""
    return sorted((m.start(), val) for pat, val in headings for m in re.finditer(pat, norm))


def extract_report(report_year: int):
    """Returns (indicator candidates, scope texts)."""
    path = DATA_DIR / f"{report_year}.pdf"
    rel = path.relative_to(ROOT).as_posix()
    rows, flow_pages = [], []
    for pno, text, flow in iter_pages(path):
        flow_pages.append((pno, flow))
        lines = text.split("\n")
        norms = [normalize(l) for l in lines]
        header = None
        prod_ctx = raw_ctx = None
        afr_ttl = 0  # lines remaining after the "Alternatif Yakıt Kullanım Oranı %" heading
        for raw, norm in zip(lines, norms):
            prod_h = find_headings(norm, PROD_HEADINGS)
            raw_h = find_headings(norm, RAW_HEADINGS)
            if re.fullmatch(r"\s*alternatif yakit kullanim orani\s*%\s*", norm):
                afr_ttl = 4
                continue
            taken = []
            for item, pat, ctx_type in ITEM_PATTERNS:
                if ctx_type == "afr" and afr_ttl <= 0:
                    continue
                for m in re.finditer(pat, norm):
                    if any(s <= m.start() < e or m.start() <= s < m.end() for s, e in taken):
                        continue
                    parsed = parse_after_label(norm, m.end())
                    if not parsed:
                        continue
                    gap, nums, after = parsed
                    if not nums or all(t == "-" for t in nums):
                        continue
                    if re.fullmatch(r"(19|20)\d\d", nums[0]) and after.startswith("yil"):
                        continue  # "... 2021 yılında" ("in 2021") narrative
                    unit_m = UNIT_RE.search(gap)
                    unit = unit_m.group(1) if unit_m else ""
                    if not unit:
                        am = AFTER_UNIT_RE.match(after)
                        unit = am.group(1) if am else ""
                    if not unit and any("%" in t for t in nums):
                        unit = "%"
                    if not unit:
                        continue  # unitless single numbers are mostly footnotes / page numbers
                    taken.append((m.start(), m.end()))

                    # Scope
                    pre_full = norm[max(0, m.start() - 30):m.start()]
                    pre = re.split(r"[\d%»·|]", pre_full)[-1]
                    parts = scope_of(item, pre, m.group(0), gap)
                    note = []
                    if ctx_type == "prod":
                        cur = [v for p, v in prod_h if p < m.start()]
                        section = cur[-1] if cur else prod_ctx
                        if section is None or section == "SALES":
                            continue  # sales volume or short label without context: not production
                        parts.insert(0, section)
                        if re.search(r"cimento[*1]", m.group(0)):
                            note.append("label carries a footnote marker (the report may have a 'Klinker dahildir' (clinker included) note)")
                    if ctx_type == "raw":
                        cur = [v for p, v in raw_h if p < m.start()]
                        section = cur[-1] if cur else raw_ctx
                        parts.insert(0, section or "raw material section heading not found")
                    if ctx_type == "afr":
                        parts = ["grey" if "gri" in m.group(0) else "white"]
                        if m.group(0).startswith("*"):
                            note.append("footnote: the white cement rate may refer to the Buñol plant only")
                    scope = "; ".join(parts) if parts else "unspecified"

                    name = item
                    if item == "alt_fuel_rate":
                        name = {"grey+white": "alt_fuel_rate", "grey": "alt_fuel_rate_grey",
                                "white": "alt_fuel_rate_white"}.get(parts[0] if parts else "", "alt_fuel_rate")
                        if not parts:
                            note.append("grey/white split not stated in the line")

                    if header is None:
                        header = page_header_years(norms) or []
                    rows.extend(make_rows(name, nums, unit, scope, note, raw.strip(), pno,
                                          header, report_year, rel))
            # Update the section context for the following lines
            if prod_h:
                prod_ctx = prod_h[-1][1]
            if raw_h:
                raw_ctx = raw_h[-1][1]
            afr_ttl -= 1

        # I-REC: label and numbers may be on different lines
        for i, norm in enumerate(norms):
            if not IREC_LABEL.search(norm):
                continue
            window = norms[i:i + 4]
            if "i-rec" not in " ".join(window):
                continue  # other certificate types (PPA etc.) are not wanted
            for j, line in enumerate(norms[i:i + 3], start=i):
                mm = re.search(r"\bmwh ((?:[\d.,]+ ?)+)", line)
                if mm:
                    nums = mm.group(1).split()
                    if header is None:
                        header = page_header_years(norms) or []
                    raw_txt = " / ".join(l.strip() for l in lines[i:j + 1])
                    note = [] if j == i else ["label and numbers on different lines; numbers taken from the line after the label"]
                    rows.extend(make_rows("irec_mwh", nums, "mwh", "I-REC", note, raw_txt, pno,
                                          header, report_year, rel))
                    break
    return rows, extract_boundaries(report_year, flow_pages)


def make_rows(name, nums, unit, scope, note, raw, pno, header, report_year, rel):
    """Assigns years to the number tokens and builds the candidate rows."""
    note = list(note)
    if len(nums) == 1:
        years, uncertain = [report_year], True
        note.append("single value; assumed to belong to the report year")
    elif header and len(nums) >= len(header):
        years, uncertain = header, False
        if len(nums) > len(header):
            note.append(f"more numbers in the line than in the header; first {len(header)} used")
        nums = nums[:len(header)]
    else:
        years, uncertain = [None] * len(nums), True
        why = ("no year header on the page" if not header
               else f"number count ({len(nums)}) does not match the year header ({header})")
        note.append(f"{why}; no year assigned (order: {', '.join(nums)})")
    out = []
    for yr, tok in zip(years, nums):
        if tok == "-":
            continue
        value, vnote = parse_value(tok, unit)
        out.append({
            "item": name,
            "year": yr,
            "year_uncertain": uncertain,
            "value": value,
            "unit": unit,
            "report_year": report_year,
            "source_file": rel,
            "source_page": pno,
            "scope": scope,
            "raw_text": raw,
            "note": "; ".join(note + ([vnote] if vnote else [])),
        })
    return out


def extract_boundaries(report_year: int, flow_pages):
    """Quotes the scope/boundary sentences verbatim from the pages read in text-flow order."""
    out, seen = [], {}
    for pattern, note in BOUNDARY_ANCHORS.get(report_year, []):
        found = False
        for pno, flat in flow_pages:
            for m in re.finditer(pattern, flat):
                text = m.group(0).strip()
                found = True
                if text in seen:  # the same footnote repeats on many pages
                    seen[text]["other_pages"].append(pno)
                    continue
                row = {"report_year": report_year, "source_page": pno, "boundary_text": text,
                       "note": note, "other_pages": []}
                seen[text] = row
                out.append(row)
        if not found:
            out.append({"report_year": report_year, "source_page": None, "boundary_text": None,
                        "note": f"WARNING: anchor phrase not found ({pattern})", "other_pages": []})
    for row in out:
        if row["other_pages"]:
            row["note"] += f"; the same text also appears on pages: {', '.join(map(str, row['other_pages']))}"
        del row["other_pages"]
    return out


def iter_pages(path):
    """Yields (page no, plain text, single-line text read in text-flow order)."""
    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            flow = page.extract_text(use_text_flow=True) or ""
            yield pno, text, re.sub(r"\s*\n\s*", " ", flow)


def build_conflicts(df: pd.DataFrame) -> pd.DataFrame:
    """Lists all candidates where the same item/year/scope/unit has more than one distinct value."""
    d = df.dropna(subset=["value", "year"]).copy()
    d["unit_key"] = d["unit"].str.lower()
    parts = []
    for _, g in d.groupby(["item", "year", "scope", "unit_key"]):
        vals = g["value"].round(6)
        if vals.nunique() > 1:
            g = g.copy()
            g["n_distinct_values"] = vals.nunique()
            g["reports"] = ", ".join(str(r) for r in sorted(g["report_year"].unique()))
            g["diff_pct"] = round((g["value"].max() / g["value"].min() - 1) * 100, 2) if g["value"].min() else None
            parts.append(g)
    cols = ["item", "year", "scope", "unit", "value", "report_year", "source_page", "year_uncertain",
            "n_distinct_values", "reports", "diff_pct", "raw_text", "note"]
    if not parts:
        return pd.DataFrame(columns=cols)
    return pd.concat(parts)[cols].sort_values(["item", "year", "scope", "report_year", "source_page"])


def main():
    rows, boundaries = [], []
    for ry in YEARS:
        r, b = extract_report(ry)
        rows.extend(r)
        boundaries.extend(b)
        print(f"{ry}: {len(r)} candidates, {len(b)} scope texts")
    df = pd.DataFrame(rows).drop_duplicates()
    df["year"] = df["year"].astype("Int64")
    df = df.sort_values(["item", "year", "report_year", "source_page"]).reset_index(drop=True)
    TABLE_OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(TABLE_OUT, index=False, encoding="utf-8")

    conflicts = build_conflicts(df)
    CHECK_OUT.parent.mkdir(parents=True, exist_ok=True)
    conflicts.to_csv(CHECK_OUT, index=False, encoding="utf-8")
    n_groups = conflicts.groupby(["item", "year", "scope", "unit"]).ngroups if len(conflicts) else 0
    print(f"\nCandidates: {TABLE_OUT.relative_to(ROOT)} ({len(df)} rows)")
    print(f"Conflicts : {CHECK_OUT.relative_to(ROOT)} ({n_groups} groups, {len(conflicts)} rows)")

    bnd = pd.DataFrame(boundaries, columns=["report_year", "source_page", "boundary_text", "note"])
    bnd["source_page"] = bnd["source_page"].astype("Int64")
    bnd.to_csv(BOUNDARY_OUT, index=False, encoding="utf-8")
    n_missing = bnd["boundary_text"].isna().sum()
    print(f"Scope     : {BOUNDARY_OUT.relative_to(ROOT)} ({len(bnd)} rows, anchors not found: {n_missing})")


if __name__ == "__main__":
    main()
