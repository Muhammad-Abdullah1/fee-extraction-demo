# Construction / Development Fee Extraction — Proof of Concept

A small demo that proves the core idea: pull real fees from official
city/county sources, store them in a structured form, and keep every
extracted number tied to the exact line of source text it came from.

The bar is **traceability**, not coverage. Every number you see in the
UI is one click away from the literal line in the original PDF.

---

## What it proves

1. **Real extraction works** — 941 fee records pulled from real,
   public PDFs published by the City of Calimesa and the Riverside
   County Department of Environmental Health.
2. **Structured storage works** — `sources`, `fees`, and `change_logs`
   tables; one row per fee, every fee linked to a source row.
3. **Source traceability works** — every `fees` row stores the exact
   `raw_snippet` it was parsed from, plus a link back to the source's
   public URL. The fee detail page shows the snippet in a `<pre>` block
   right next to the parsed amount.
4. **No invented numbers** — the parsers only emit a fee record when
   they have a literal `$N` token (or `N%`) in the source. AI is **not**
   used anywhere in the value path.
5. **Honest uncertainty** — if the same `(department, subheader,
   fee_name, unit)` produces two different amounts in the same source,
   the system stores both rows and flags both as `conflicting`. It
   never silently picks one. Image-only PDFs are kept as `Source`
   records with a note explaining why no fees were extracted.

---

## Project structure

```
demo/
├── README.md
├── requirements.txt
├── app/
│   ├── __init__.py
│   ├── main.py                # FastAPI app
│   ├── db.py                  # SQLAlchemy engine/session, init_db()
│   ├── models.py              # Source, Fee, ChangeLog
│   ├── static/style.css
│   └── templates/
│       ├── base.html
│       ├── index.html         # filtered fees table
│       ├── fee.html           # one fee + raw evidence
│       ├── sources.html       # ingested sources index
│       └── source.html        # one source + raw extracted text
├── extractor/
│   ├── __init__.py
│   ├── sources.py             # registry of public URLs we ingest
│   ├── fetch.py               # cached HTTP fetch (Playwright hook)
│   ├── pdf_extract.py         # pdfplumber wrapper (text + tables)
│   └── parsers.py             # one parser per source
├── scripts/
│   ├── __init__.py
│   └── run_extraction.py      # CLI: fetch → parse → store
├── fixtures/                  # cached copies of the official PDFs
│   ├── calimesa_master_fee_schedule_2025-09-01.pdf
│   ├── calimesa_dif_schedule_2017.pdf
│   └── rivco_eh_community_events_2024-07.pdf
└── data/
    └── fees.db                # SQLite, created at runtime
```

## Setup

Requires Python 3.10+.

```bash
cd demo
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# build the SQLite DB and ingest the three configured sources
python -m scripts.run_extraction --reset

# run the web UI
c
# then open http://127.0.0.1:8000
```

`run_extraction.py` reads from `fixtures/*.pdf` if present (the demo
ships with cached copies of the public PDFs). If the cache is missing
it will download the file from the official URL on first run.

CLI options:

```bash
python -m scripts.run_extraction --reset            # wipe DB and reload
python -m scripts.run_extraction --source calimesa_master_fee_2025
```

---

## Sources used

| Key | Jurisdiction | Department | URL |
|---|---|---|---|
| `calimesa_master_fee_2025` | City of Calimesa | Multiple | https://www.calimesa.gov/DocumentCenter/View/1856/Calimesa-Master-Fee-Schedule-Effective-090125 |
| `rivco_eh_community_events_2024` | Riverside County | Environmental Health | https://rivcoeh.org/sites/g/files/aldnop361/files/2024-06/Temporary-Food-Facilities-Permit-Fees-for-Community-Events%207-24.pdf |
| `calimesa_dif_2017` | City of Calimesa | Development | https://www.calimesa.gov/DocumentCenter/View/908 |

The Calimesa Master Fee Schedule alone covers the **Fire Department**
and **Land Use Management** target categories (via the Fire and
Planning sections), plus most **Development fees** (Building, Public
Works). The Riverside County EH PDF covers the **Environmental Health**
target category. **Water** is not covered in the demo — see "Blockers"
below.

---

## Database schema

Mirrors the spec.

```text
sources(id, url, title, jurisdiction, department, source_type,
        fetched_at, raw_content, raw_snippet, local_path, notes)

fees(id, source_id → sources.id, fee_name, amount, amount_text,
     unit, department, jurisdiction, category, confidence,
     validation_flag, raw_snippet, notes, extracted_at)

change_logs(id, source_id, fee_id, field_name, old_value,
            new_value, detected_at)
```

`amount` is the parsed float (nullable when the source value is a
percentage or "Actual Cost"); `amount_text` is the literal token from
the source (`"$ 223"`, `"$1,310.00"`, `"3.75%"`).

`change_logs` is wired up in the model layer for future "did the fee
change since last extraction" support; the demo doesn't write to it
yet — it's there so you can see the planned shape.

---

## Validation flags

| Flag | Meaning |
|---|---|
| `verified` | Parsed cleanly from a labeled fee row with a numeric amount. |
| `needs_review` | Amount couldn't be parsed numerically (percentages, "Actual Cost", "Deposit"), or the unit/section context was missing. |
| `conflicting` | Within one source, the same `(department, subheader, fee_name, unit)` produced two or more different amounts. Both rows are kept. |
| `incomplete` | Reserved (no current rows). Kept in the schema for partial extractions. |

`confidence` is a float in `[0, 1]` reflecting **extraction clarity**,
not model confidence: 0.95 for a clean tabular cell, 0.9 for a
recognised line in the master fee schedule, lower when section context
or unit is missing.

---

## How traceability works (concretely)

For every fee row in `fees`:

- `source_id` → exactly one `sources` row, which carries the public
  `url`, the `title` of the document, when it was `fetched_at`, the
  `local_path` of the cached file, and the full `raw_content`.
- `raw_snippet` on the fee row stores the literal line (or
  `Row: a | b | c` for table cells) the amount was read from.
- `extracted_at` records when the demo's parser produced this row.

The fee detail page (`/fees/{id}`) renders all of the above on one
screen: parsed amount, raw snippet in a `<pre>` block, plus the source
link clickable straight back to the public PDF.

To eyeball traceability:

1. Open `/`, search for "Fire Booster Pump" or filter by category
   "Environmental Health".
2. Click any row. The "Raw source evidence" block shows the exact
   line from the PDF.
3. Click the source URL. The browser opens the official PDF — the
   amount in the snippet appears verbatim in the document.

---

## Assumptions

- **SQLite, not Postgres.** The deliverables list says SQLite and the
  storage line says Postgres; we picked SQLite because the demo is
  meant to run with a single command and no infra. Swapping is a
  one-line change in `app/db.py` (`DATABASE_URL`).
- **No AI in the value path.** The spec says AI may be used for
  cleaning and structuring but not for deciding amounts. To keep this
  guarantee bulletproof we did not call any AI model — parsers are
  deterministic regex + table walks. A future iteration could add a
  cleanup pass for messy section headers, but it would never see the
  numeric column.
- **Cached fixtures preferred.** The demo ships with the three real
  PDFs in `fixtures/` so it's reproducible offline. The fetcher will
  re-download from the official URL if the cache is missing.
- **Playwright stub only.** Both working sources are static PDFs, so
  the included fetcher is plain `requests`. A Playwright fallback hook
  is left in `extractor/fetch.py` for future JS-rendered pages.

---

## Blockers and messy source issues found

These are real, not hypothetical — they came up while building the
demo. The system surfaces each one clearly rather than hiding it.

1. **Calimesa DIF 2017 PDF is image-only.** `pdfplumber` returns no
   extractable text. The pipeline records the source row with a note
   ("PDF returned no extractable text (likely scanned image)") and
   emits zero fees. To finish this source we'd need OCR (Tesseract or
   the `ocrmypdf` toolchain). Visible in the UI on `/sources/3`.
2. **Riverside County Planning fee schedule (`planning.rctlma.org`)
   and Calimesa zoning code on `codepublishing.com` returned HTTP 403**
   when fetched with plain `requests` — likely a WAF or
   bot-protection. These would need a Playwright-driven fetch with a
   real browser fingerprint. Left out of the demo for that reason.
3. **Calimesa Master Fee Schedule has tiered "Plan Check" tables.**
   The same fee name (e.g. "Each Additional 100 sq. ft. or fraction
   there of") appears multiple times on the same page with different
   amounts because each row applies to a different occupancy or
   construction-type column above. The demo correctly flags these as
   `conflicting` and stores all of them — refusing to silently choose
   a winner is the desired behavior. About 250 of the 941 records are
   in this state; they're real fees with legitimate ambiguity that a
   human still needs to disambiguate by reading the column headers.
4. **One source row contains a typo in the public PDF.** The Riverside
   County EH PDF has `"$5, 120.00"` (note the space) for the 36–40
   vendor temporary-event fee. The parser preserves the literal text,
   sets the amount to `5120.00`, lowers confidence to 0.7, and adds a
   note. See fee id 919 in the demo DB.
5. **No "Water" category source.** The two working sources don't
   contain Water utility fees. In Riverside County water rates are set
   by individual special districts (EMWD, WMWD, RPU, etc.) rather than
   by the County itself. Adding one of those districts' rate sheets
   would be a one-source extension to `extractor/sources.py` plus a
   parser.

---

## Sample queries

```sql
-- a few verified Fire Department fees
SELECT fee_name, amount_text, unit, raw_snippet
FROM fees
WHERE category = 'Fire Department' AND validation_flag = 'verified'
LIMIT 10;

-- everything the system isn't sure about
SELECT fee_name, amount_text, validation_flag, notes
FROM fees
WHERE validation_flag != 'verified'
ORDER BY validation_flag;
```

---

## What's deliberately out of scope

Auth, dashboards, admin tools, multi-tenant separation, change-detection
runs, Postgres infra, scheduled re-extractions, OCR for image PDFs,
fee-history analytics. All easy to add on top once the trace-back
guarantee is in place — which is what this proof of concept is for.
