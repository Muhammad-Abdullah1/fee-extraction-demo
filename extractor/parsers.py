"""Per-source parsers.

Each parser is deterministic. It walks the extracted text/tables and
emits FeeRecord objects. Every record is tied to the *exact* line or
table cell it came from via raw_snippet — no value is invented.

Rules of the road:
- We never *compute* a fee amount. Only literal numbers seen in the source.
- A line/cell with no parseable number does not produce a record.
- If we can't categorize a row confidently, we still keep it but lower
  confidence and set validation_flag = "needs_review".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from extractor.pdf_extract import PdfPage, all_text


# A dollar amount can be: $ 223 | $223.00 | $1,310.00 | $ 0.20
DOLLAR_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")
# A standalone percentage (used for credit-card pass-through fees etc.)
PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)%")


@dataclass
class FeeRecord:
    fee_name: str
    amount: Optional[float]
    amount_text: str
    unit: Optional[str]
    department: Optional[str]
    jurisdiction: str
    category: Optional[str]
    raw_snippet: str
    confidence: float
    validation_flag: str
    notes: Optional[str] = None
    extras: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Calimesa Master Fee Schedule
# ---------------------------------------------------------------------------

# Top-level sections that appear as their own header line in the PDF.
# When we see one of these, we update the current section.
CALIMESA_SECTIONS = {
    "City Clerk": ("Administration", "City Clerk"),
    "Finance": ("Administration", "Finance"),
    "Fire": ("Fire Department", "Fire"),
    "Building": ("Development fees", "Building"),
    "Engineering": ("Land Use Management", "Engineering"),
    "Planning": ("Land Use Management", "Planning"),
    "Public Works": ("Development fees", "Public Works"),
    "Code Enforcement": ("Code Enforcement", "Code Enforcement"),
}


# Recognised unit phrases. Order matters — longest first.
UNIT_PATTERNS = [
    r"Per Building, system, or plan type",
    r"Per Building or plan type",
    r"Per building or system",
    r"Per system or plan type",
    r"Per Month / Per Regulated Space / Per Park",
    r"Each add'l \d+(?:,\d{3})* sq\. ft\. per building",
    r"Each add'l \d+ devices, per system",
    r"Each add'l \d+ Head",
    r"Per addl\. \d+ Head",
    r"Per System",
    r"Per system",
    r"Per Pump",
    r"Per tank",
    r"Per booth",
    r"Per building",
    r"Per permit",
    r"Per hour",
    r"Per Page",
    r"Per CD",
    r"Per Transaction",
    r"Each inspection after 2nd failed inspection",
    r"Each",
]
UNIT_RE = re.compile(r"\s(" + "|".join(UNIT_PATTERNS) + r")\s")


def _parse_amount(raw: str) -> tuple[Optional[float], str]:
    m = DOLLAR_RE.search(raw)
    if m:
        try:
            return float(m.group(1).replace(",", "")), m.group(0)
        except ValueError:
            return None, m.group(0)
    p = PERCENT_RE.search(raw)
    if p:
        return None, p.group(0)
    return None, ""


def _split_calimesa_line(line: str) -> Optional[tuple[str, Optional[str], str, str]]:
    """Return (fee_name, unit, amount_text, regulation) or None.

    The Calimesa Master Fee Schedule lays each fee out as:
        <fee name>  <unit>  $<amount>  <regulation note>
    Columns are separated by spaces only — there is no delimiter — so
    we anchor on the dollar sign and the recognised unit phrases.
    """
    dollar_match = DOLLAR_RE.search(line)
    if not dollar_match:
        return None

    # Everything after the dollar amount is the regulation note.
    after = line[dollar_match.end():].strip()
    before = line[: dollar_match.start()].rstrip()

    # Find the rightmost unit phrase in `before`. Whatever sits to the
    # left of it is the fee_name.
    unit = None
    fee_name = before
    for m in UNIT_RE.finditer(" " + before + " "):
        unit = m.group(1)
    if unit is not None:
        # split on the *last* occurrence of unit
        idx = before.rfind(unit)
        if idx > 0:
            fee_name = before[:idx].strip()

    return fee_name.strip(" :"), unit, dollar_match.group(0), after


def parse_calimesa_master(pages: list[PdfPage], jurisdiction: str) -> list[FeeRecord]:
    out: list[FeeRecord] = []
    current_top: Optional[str] = None        # e.g. "Fire"
    current_subheader: Optional[str] = None  # closest header line above

    seen_keys: dict[tuple, FeeRecord] = {}   # detect within-source conflicts

    for page in pages:
        for raw_line in page.text.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("Master Fee Schedule") or line.startswith("City of Calimesa"):
                continue
            if line.startswith("Page ") and "of" in line:
                continue
            if line.startswith("Fee Name "):
                continue

            # Section header?
            if line in CALIMESA_SECTIONS:
                current_top = line
                current_subheader = None
                continue

            parsed = _split_calimesa_line(line)
            if parsed is None:
                # Not a fee row — could be a subsection header. Track it
                # so the immediate next fee_name has helpful context.
                if len(line) <= 80 and "$" not in line and "%" not in line:
                    current_subheader = line
                continue

            fee_name, unit, amount_text, _regulation = parsed
            if not fee_name:
                # A leaf row whose name was on the previous line. Pull
                # in the subheader as the name.
                if current_subheader:
                    fee_name = current_subheader
                else:
                    continue

            amount, _ = _parse_amount(amount_text)
            category, department = CALIMESA_SECTIONS.get(
                current_top or "", (None, current_top)
            )

            confidence = 0.9
            flag = "verified"
            notes_parts = []
            if current_top is None:
                confidence = 0.6
                flag = "needs_review"
                notes_parts.append("section context not detected")
            if amount is None:
                confidence = min(confidence, 0.5)
                flag = "needs_review"
                notes_parts.append("amount not numeric (e.g. percentage or text)")
            if unit is None:
                confidence = min(confidence, 0.7)
                notes_parts.append("unit not identified")

            rec = FeeRecord(
                fee_name=fee_name,
                amount=amount,
                amount_text=amount_text,
                unit=unit,
                department=department,
                jurisdiction=jurisdiction,
                category=category,
                raw_snippet=raw_line,
                confidence=confidence,
                validation_flag=flag,
                notes="; ".join(notes_parts) or None,
                extras={"page": page.page_number, "section": current_top, "subheader": current_subheader},
            )

            # Conflict detection: same (department, subheader, fee_name, unit)
            # with a different amount within the same source. Including the
            # subheader lets us tell apart e.g. "Plan Review" under NFPA 13
            # vs NFPA 13D — those are different fees, not conflicts.
            key = (department, current_subheader, fee_name, unit)
            existing = seen_keys.get(key)
            if existing and existing.amount_text != rec.amount_text:
                existing.validation_flag = "conflicting"
                existing.notes = (existing.notes + "; " if existing.notes else "") + (
                    f"conflicts with {rec.amount_text} on page {page.page_number}"
                )
                rec.validation_flag = "conflicting"
                rec.notes = (rec.notes + "; " if rec.notes else "") + (
                    f"conflicts with earlier {existing.amount_text}"
                )
            else:
                seen_keys[key] = rec

            out.append(rec)
    return out


# ---------------------------------------------------------------------------
# Riverside County Environmental Health - Community Events
# ---------------------------------------------------------------------------

def _money_to_float(s: str) -> Optional[float]:
    if not s:
        return None
    m = DOLLAR_RE.search(s)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "").replace(" ", ""))
    except ValueError:
        return None


def parse_rivco_eh_community_events(pages: list[PdfPage], jurisdiction: str) -> list[FeeRecord]:
    """The PDF contains three clean tables, one per fee program.

    Page 1 table 0:  Group Permit fees (Occasional & Temporary)
    Page 2 table 0:  Individual Vendor permit fees (Occasional & Temporary)
    Page 2 table 1:  Event Organizer permit fees
    """
    out: list[FeeRecord] = []
    department = "Environmental Health"
    category = "Environmental Health"

    def _emit(fee_name: str, raw_cell: str, raw_snippet: str, notes: str | None = None):
        amount = _money_to_float(raw_cell)
        flag = "verified" if amount is not None else "needs_review"
        confidence = 0.95 if amount is not None else 0.4
        notes_extra = []
        if amount is None:
            notes_extra.append("could not parse amount")
        # Detect known formatting glitch in source ("$5, 120.00")
        if "," in raw_cell and re.search(r",\s\d", raw_cell):
            notes_extra.append("source has whitespace inside number; preserved verbatim")
            flag = "needs_review"
            confidence = min(confidence, 0.7)
        out.append(FeeRecord(
            fee_name=fee_name,
            amount=amount,
            amount_text=raw_cell.strip(),
            unit="Per event",
            department=department,
            jurisdiction=jurisdiction,
            category=category,
            raw_snippet=raw_snippet,
            confidence=confidence,
            validation_flag=flag,
            notes=("; ".join(notes_extra) if notes_extra else notes),
        ))

    # --- Group permit table ---
    if pages and pages[0].tables:
        t = pages[0].tables[0]
        # header expected: ['Number of Vendors', 'Occasional Events', 'Temporary Events']
        header = [c.strip() if c else "" for c in t[0]]
        if "Number of Vendors" in (header[0] or "") and len(header) >= 3:
            for row in t[1:]:
                if not row or not row[0]:
                    continue
                vendors, occ, temp = (row + [None, None, None])[:3]
                snippet = " | ".join(str(c) for c in row if c is not None)
                _emit(
                    f"Group Permit ({vendors.strip()} vendors) - Occasional Event",
                    occ or "",
                    f"Row: {snippet}",
                )
                _emit(
                    f"Group Permit ({vendors.strip()} vendors) - Temporary Event",
                    temp or "",
                    f"Row: {snippet}",
                )

    # --- Individual vendor + Event Organizer tables on page 2 ---
    if len(pages) >= 2:
        p2 = pages[1]
        for tbl in p2.tables:
            if not tbl:
                continue
            header = [c.strip() if c else "" for c in tbl[0]]
            if header and header[0].lower().startswith("facility type"):
                for row in tbl[1:]:
                    if not row or not row[0]:
                        continue
                    facility, occ, temp = (row + [None, None, None])[:3]
                    snippet = " | ".join(str(c) for c in row if c is not None)
                    _emit(
                        f"Individual Vendor Permit - {facility.strip()} - Occasional Event",
                        occ or "",
                        f"Row: {snippet}",
                    )
                    _emit(
                        f"Individual Vendor Permit - {facility.strip()} - Temporary Event",
                        temp or "",
                        f"Row: {snippet}",
                    )
            elif header and "Event Organizer" in (header[0] or ""):
                for row in tbl[1:]:
                    if not row or not row[0]:
                        continue
                    label, amt = (row + [None, None])[:2]
                    snippet = " | ".join(str(c) for c in row if c is not None)
                    _emit(
                        f"Event Organizer Permit - {label.strip()}",
                        amt or "",
                        f"Row: {snippet}",
                    )

    return out


# ---------------------------------------------------------------------------
# Calimesa DIF Schedule (image-only PDF — flagged, no fees emitted)
# ---------------------------------------------------------------------------

CALIMESA_DIF_COMPONENTS = [
    "Law Enforcement Facilities",
    "Fire Protection Facilities",
    "Streets and Traffic Facilities",
    "Storm Drainage Collection Facilities",
    "General Government Facilities",
    "Library Space and Collection Items",
    "Park Land and Park Improvements",
    "Total",
]

CALIMESA_DIF_COMPONENT_CATEGORY = {
    "Fire Protection Facilities":         ("Fire Department",     "Fire Protection (DIF)"),
    "Streets and Traffic Facilities":     ("Land Use Management", "Streets & Traffic (DIF)"),
    "Park Land and Park Improvements":    ("Land Use Management", "Parks (DIF)"),
    "Storm Drainage Collection Facilities":("Land Use Management","Storm Drainage (DIF)"),
    "Law Enforcement Facilities":         ("Development fees",    "Law Enforcement (DIF)"),
    "General Government Facilities":      ("Development fees",    "General Government (DIF)"),
    "Library Space and Collection Items": ("Development fees",    "Library (DIF)"),
    "Total":                              ("Development fees",    "Total DIF (sum of components)"),
}

# OCR text rows look like:
# "Detached Dwelling $224.00 $837.00 $3,852.00 $3,682.00 $3,024.00 $794.00 $6,456.00 $18,869.00 per Unit"
# "Retail/Service/Offic $0,157 $0.055, $6.762| $0.55] $0.191) No Fee No Fee $7.716 per S.F"
# OCR noise types observed: comma instead of decimal ($0,157), trailing
# punctuation ($0.55], $0.191)), digits replacing decimals ($1,122.03).
# We accept ANY numeric token, store the literal OCR text, and flag every
# value needs_review so a human verifies against the original image.
CALIMESA_DIF_LAND_USES = [
    ("Detached Dwelling",          re.compile(r"^Detached\s+Dwelling\b"),       "per Unit"),
    ("Attached Dwelling",          re.compile(r"^Attached\s+Dwelling\b"),       "per Unit"),
    ("Mobile Home",                re.compile(r"^Mobile\s+Home\b"),             "per Unit"),
    ("Senior Restricted",          re.compile(r"^Senior\s+Restricted\b"),       "per Unit"),
    ("Assisted Living",            re.compile(r"^Assisted\s+Living\b"),         "per Unit"),
    ("Commercial",                 re.compile(r"^Commercial\b"),                "per Unit"),
    ("Retail/Service/Office",      re.compile(r"^Retail/Service/Offic"),        "per sq. ft."),
    ("Industrial/Business",        re.compile(r"^Industrial/Business\b"),       "per sq. ft."),
    ("Institutional Uses",         re.compile(r"^Institutional\s+Uses\b"),      "per sq. ft."),
]

# OCR-tolerant amount token pattern: $0.157 $0,157 $1,122.03 $7.716 No Fee
CALIMESA_DIF_TOKEN_RE = re.compile(
    r"\$[\d,]+(?:[.,]\d{1,3})?|No\s+Fee|N/A",
    re.IGNORECASE,
)


def parse_calimesa_dif(pages: list[PdfPage], jurisdiction: str) -> list[FeeRecord]:
    out: list[FeeRecord] = []
    for page in pages:
        text = page.text or ""
        for raw_line in text.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            # Find which land-use this row is for.
            land_use_unit = None
            for name, pat, unit in CALIMESA_DIF_LAND_USES:
                if pat.search(line):
                    land_use_unit = (name, unit)
                    break
            if not land_use_unit:
                continue
            land_use, unit = land_use_unit

            tokens = CALIMESA_DIF_TOKEN_RE.findall(line)
            if len(tokens) < len(CALIMESA_DIF_COMPONENTS):
                # Couldn't tokenize the row reliably — record a single
                # needs_review entry so the operator sees it.
                out.append(FeeRecord(
                    fee_name=f"DIF — {land_use} (UNPARSED ROW)",
                    amount=None, amount_text="",
                    unit=unit,
                    department="Calimesa Development Impact Fee",
                    jurisdiction=jurisdiction,
                    category="Development fees",
                    raw_snippet=raw_line,
                    confidence=0.2,
                    validation_flag="needs_review",
                    notes=f"OCR row produced only {len(tokens)} amount tokens; expected {len(CALIMESA_DIF_COMPONENTS)}. Verify against original PDF.",
                ))
                continue

            tokens = tokens[: len(CALIMESA_DIF_COMPONENTS)]

            for component, tok in zip(CALIMESA_DIF_COMPONENTS, tokens):
                category, sub = CALIMESA_DIF_COMPONENT_CATEGORY[component]

                # Normalise a small set of OCR errors but never invent.
                # Treat "$0,157" as "$0.157" only when the second character
                # after the comma is a digit and the integer part is a
                # single zero (i.e. an obvious decimal-comma swap). Any
                # other ambiguity stays in raw text and we leave amount=None.
                cleaned = tok
                ocr_fix_note = None
                m_swap = re.fullmatch(r"\$0,(\d{1,3})", tok)
                if m_swap:
                    cleaned = f"$0.{m_swap.group(1)}"
                    ocr_fix_note = f"OCR swap fixed: '{tok}' -> '{cleaned}'"

                if cleaned.lower().replace(" ", "") == "nofee" or cleaned.lower() == "n/a":
                    amount = None
                    amount_text = tok
                    flag = "needs_review"
                    notes = "source records 'No Fee' for this component (OCR layer); verify against original."
                else:
                    try:
                        amount = float(cleaned.lstrip("$").replace(",", ""))
                        amount_text = tok
                        flag = "needs_review"  # OCR-derived: always require human verification
                        notes = "Value parsed from OCR layer of an image-only PDF. Verify against original document before relying on it."
                        if ocr_fix_note:
                            notes = ocr_fix_note + "; " + notes
                    except ValueError:
                        amount = None
                        amount_text = tok
                        flag = "needs_review"
                        notes = f"OCR token '{tok}' could not be parsed as a number."

                out.append(FeeRecord(
                    fee_name=f"DIF — {land_use} — {component}",
                    amount=amount,
                    amount_text=amount_text,
                    unit=unit,
                    department=f"Calimesa DIF — {sub}",
                    jurisdiction=jurisdiction,
                    category=category,
                    raw_snippet=raw_line,
                    confidence=0.6,  # OCR-derived
                    validation_flag=flag,
                    notes=notes,
                    extras={"land_use": land_use, "component": component, "page": page.page_number},
                ))
    return out


# ---------------------------------------------------------------------------
# Riverside County DIF Ordinance 659.14
# ---------------------------------------------------------------------------

# Column order in every "Maximum Justified Development Impact Fee Schedule"
# table in the ordinance. Verified from the column headers on pages 7-12.
DIF_COMPONENTS = [
    "Public Facilities",                # Criminal Justice Public Facilities
    "Library Construction",
    "Fire Protection",
    "Traffic Improvement Facilities",
    "Traffic Signals",
    "Regional Parks",
    "Trails",
    "Flood Control",
    "Library Books",
    "Multi-Service Centers",
    "Total",
]

# Component → (target_category, sub-department label).
DIF_COMPONENT_CATEGORY = {
    "Fire Protection":               ("Fire Department",     "Fire Protection (DIF)"),
    "Public Facilities":             ("Development fees",    "Criminal Justice Public Facilities"),
    "Library Construction":          ("Development fees",    "Library Construction"),
    "Library Books":                 ("Development fees",    "Library Books / Media"),
    "Multi-Service Centers":         ("Development fees",    "Multi-Service Centers"),
    "Traffic Improvement Facilities": ("Land Use Management", "Transportation"),
    "Traffic Signals":               ("Land Use Management", "Transportation"),
    "Regional Parks":                ("Land Use Management", "Parks & Recreation"),
    "Trails":                        ("Land Use Management", "Parks & Recreation"),
    "Flood Control":                 ("Land Use Management", "Flood Control"),
    "Total":                         ("Development fees",    "Total DIF (sum of components)"),
}

# Land-use rows we expect to find inside each table. Order is informational
# only — we match by line prefix.
DIF_LAND_USES = [
    ("Residential",     re.compile(r"^Residential\s+per\s+Sq")),
    ("Commercial",      re.compile(r"^Commercial\b(?!\s*\$|\s+\$)")),  # not "Commercial $"
    ("Office",          re.compile(r"^Office\d?\b")),
    ("Industrial",      re.compile(r"^Industrial\b")),
    ("Surface Mining",  re.compile(r"^Surface\s+Mining\b")),
    ("Wineries",        re.compile(r"^Wineries\b")),
    ("Warehouse",       re.compile(r"^Warehouse\b")),
]

DIF_AREA_PLAN_RE = re.compile(
    r"Maximum Justified Development Impact Fee Schedule,\s*(.+?)\s*$"
)


def _dif_tokenize(rest: str) -> list[str]:
    """Tokenize the amount segment of a DIF row.

    Returns a list of tokens, each one of: "$X.YY", "$-", "X.YY", "-", or "N/A".
    Collapses "$ 0.51" to "$0.51" first so simple split works.
    """
    s = re.sub(r"\$\s+", "$", rest)
    raw = s.split()
    out = []
    for tok in raw:
        # accept tokens that look like amounts; ignore footnote markers etc.
        if re.fullmatch(r"\$-|\$[\d,]+(?:\.\d{1,3})?|\d+(?:\.\d{1,3})?|-|N/A", tok):
            out.append(tok)
    return out


def _dif_token_to_amount(tok: str) -> tuple[Optional[float], str]:
    """Return (parsed_amount, literal_text). literal_text preserves the source token."""
    if tok in ("-", "$-", "N/A"):
        return None, tok
    if tok.startswith("$"):
        try:
            return float(tok[1:].replace(",", "")), tok
        except ValueError:
            return None, tok
    try:
        return float(tok.replace(",", "")), tok
    except ValueError:
        return None, tok


def parse_rivco_dif(pages: list[PdfPage], jurisdiction: str) -> list[FeeRecord]:
    out: list[FeeRecord] = []
    current_area_plan: Optional[str] = None

    for page in pages:
        for raw_line in page.text.split("\n"):
            line = raw_line.strip()
            if not line:
                continue

            m = DIF_AREA_PLAN_RE.search(line)
            if m:
                current_area_plan = m.group(1).strip()
                continue

            if current_area_plan is None:
                continue

            land_use = None
            for name, pat in DIF_LAND_USES:
                if pat.search(line):
                    land_use = name
                    break
            if land_use is None:
                continue

            # Strip the row label off the front. The ordinance uses two
            # forms: "Residential per Sq. Ft. <amounts>" and just
            # "<land_use> <amounts>".
            after_label = re.sub(
                r"^(Residential\s+per\s+Sq\.\s*Ft\.|Office\d?|Surface\s+Mining|"
                r"Commercial|Industrial|Wineries|Warehouse)\s*",
                "",
                line,
                count=1,
            )
            tokens = _dif_tokenize(after_label)
            if len(tokens) != len(DIF_COMPONENTS):
                # Don't invent — if the row didn't tokenize cleanly,
                # skip it and don't emit any number from it.
                continue

            for component, tok in zip(DIF_COMPONENTS, tokens):
                amount, amount_text = _dif_token_to_amount(tok)
                category, sub = DIF_COMPONENT_CATEGORY[component]

                # N/A or "-" → the ordinance says this component does
                # not apply to this land use in this area plan. Record
                # it so the table is complete; flag accordingly.
                if amount is None:
                    flag = "verified"  # the source explicitly says "N/A" / "—"
                    confidence = 0.9
                    notes = "source explicitly marks this component as N/A or — for this land use"
                else:
                    flag = "verified"
                    confidence = 0.95
                    notes = None

                out.append(FeeRecord(
                    fee_name=f"DIF — {current_area_plan} — {land_use} — {component}",
                    amount=amount,
                    amount_text=amount_text,
                    unit="per sq. ft.",
                    department=f"TLMA — {sub}",
                    jurisdiction=jurisdiction,
                    category=category,
                    raw_snippet=raw_line,
                    confidence=confidence,
                    validation_flag=flag,
                    notes=notes,
                    extras={"area_plan": current_area_plan, "land_use": land_use, "component": component},
                ))
    return out


# ---------------------------------------------------------------------------
# EMWD Consolidated Rate Schedule
# ---------------------------------------------------------------------------

# Each rate page in the EMWD schedule has a small table. Header cells
# often span two lines. We anchor on the year column "1/1/2026" (the
# current effective rate at time of writing) and read three meter-size
# rate columns (2025, 2026, 2027) per row. We emit only the 1/1/2026
# column as a fee; the other two are recorded in raw_snippet so the
# evidence is preserved.

EMWD_TARGET_YEAR_HEADER = "1/1/2026"

# Tokens like "$0.220", "$71.792", "$10,637" etc.
EMWD_DOLLAR_RE = re.compile(r"\$\s*[\d,]+(?:\.\d{1,4})?")
EMWD_TIER_HEADERS = ("Tier 1", "Tier 2", "Tier 3", "Tier 4")


def parse_emwd_rates(pages: list[PdfPage], jurisdiction: str) -> list[FeeRecord]:
    """Walk EMWD pages line-by-line. A 'fee row' is any line that:
       - is on a page that mentions a rate program (Water Daily Service Charge,
         Water Supply Reliability, Water Service Rates, etc.), and
       - contains exactly three dollar amounts (the 2025/2026/2027 columns).
    We emit the 2026 amount as the canonical fee and stash the row in
    raw_snippet so a reviewer can confirm.
    """
    out: list[FeeRecord] = []
    department = "Eastern Municipal Water District"

    for page in pages:
        text = page.text or ""
        if "$" not in text:
            continue

        # Rough page-level program label (used as fee_name prefix).
        program: Optional[str] = None
        prev_program_line: Optional[str] = None
        for raw_line in text.split("\n"):
            line = raw_line.strip()
            if not line:
                continue

            # Catch program-level headings like "Water Daily Service Charge - Domestic Retail"
            if any(kw in line for kw in (
                "Water Daily Service Charge",
                "Water Daily Supply",
                "Water Service Rates",
                "Recycled Water",
                "Wastewater",
                "Sewer Service",
                "Connection Fee",
                "Capacity Charge",
                "Standby Charge",
                "Inspection Fee",
                "Plan Check",
            )):
                program = line
                continue

            dollars = EMWD_DOLLAR_RE.findall(line)
            if len(dollars) < 3:
                continue
            # Heuristic: a real rate row has exactly 3 dollar amounts (the
            # three effective dates) OR 4 if it has a per-unit fee + 3 years.
            # Keep only the cleanest case to avoid mis-extraction.
            if len(dollars) > 4:
                continue

            # Use the *second* dollar token as the 1/1/2026 amount when
            # there are 3, third when there are 4 (4-token rows are rare;
            # they appear when a row has both an "Account Charge" and a
            # rate column). To stay conservative we only emit the 3-col case.
            if len(dollars) != 3:
                continue
            amount_text = dollars[1].replace(" ", "")
            try:
                amount = float(amount_text.lstrip("$").replace(",", ""))
            except ValueError:
                continue

            # Build a fee_name. Drop the trailing dollar columns from the
            # label part of the line.
            label = EMWD_DOLLAR_RE.split(line, maxsplit=1)[0].strip()
            label = re.sub(r"\s+", " ", label)
            if not label:
                continue
            # Skip header rows.
            if any(label.startswith(h) for h in ("Description", "Areas Served")):
                continue
            if label.endswith(("Codes:", "Service Information")):
                continue

            fee_name = f"{program} — {label}" if program else label

            # Category mapping
            lname = (program or label).lower()
            if "wastewater" in lname or "sewer" in lname:
                category = "Water"
                sub = "Wastewater / Sewer"
            elif "recycled" in lname:
                category = "Water"
                sub = "Recycled Water"
            else:
                category = "Water"
                sub = "Potable Water"

            # Confidence: high when label is non-trivial and amount parsed.
            confidence = 0.9 if len(label) > 4 else 0.6
            flag = "verified" if confidence >= 0.85 else "needs_review"

            out.append(FeeRecord(
                fee_name=fee_name,
                amount=amount,
                amount_text=amount_text,
                unit="per day (rate effective 1/1/2026)",
                department=f"{department} — {sub}",
                jurisdiction=jurisdiction,
                category=category,
                raw_snippet=raw_line,
                confidence=confidence,
                validation_flag=flag,
                notes="Three-column row (2025/2026/2027); recorded the 1/1/2026 column. All three columns preserved in raw_snippet.",
                extras={"page": page.page_number, "program": program},
            ))
    return out


def parse_pointer_only(pages: list[PdfPage], jurisdiction: str) -> list[FeeRecord]:
    """No-fee parser for documentation/pointer sources.

    Used for relationship sources like "Calimesa EH is provided by
    Riverside County DEH" or known blockers (YVWD has no public rate
    PDF). Emits zero fees; the Source row itself carries the message.
    """
    return []


PARSERS = {
    "parse_calimesa_master": parse_calimesa_master,
    "parse_rivco_eh_community_events": parse_rivco_eh_community_events,
    "parse_calimesa_dif": parse_calimesa_dif,
    "parse_rivco_dif": parse_rivco_dif,
    "parse_emwd_rates": parse_emwd_rates,
    "parse_pointer_only": parse_pointer_only,
}
