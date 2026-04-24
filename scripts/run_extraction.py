"""Run the extraction pipeline against all configured sources.

Usage:
    python -m scripts.run_extraction              # all sources
    python -m scripts.run_extraction --source key
    python -m scripts.run_extraction --reset      # wipe DB and reload
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

# Allow `python scripts/run_extraction.py` from project root.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db import SessionLocal, init_db, engine, Base  # noqa: E402
from app import models  # noqa: E402,F401
from extractor.fetch import ensure_local  # noqa: E402
from extractor.pdf_extract import extract_pdf, all_text  # noqa: E402
from extractor.sources import SOURCES, SourceSpec  # noqa: E402
from extractor.parsers import PARSERS, FeeRecord  # noqa: E402


def _ingest_source(spec: SourceSpec, session) -> tuple[int, int]:
    print(f"\n[+] {spec.key}: {spec.title}")
    print(f"    URL : {spec.url}")
    print(f"    File: {spec.local_path}")

    if spec.source_type == "pointer":
        # Pointer sources have no file to fetch. Just record the row.
        src = models.Source(
            url=spec.url,
            title=spec.title,
            jurisdiction=spec.jurisdiction,
            department=spec.department,
            source_type=spec.source_type,
            fetched_at=datetime.utcnow(),
            raw_content=spec.notes or None,
            raw_snippet=spec.notes or None,
            local_path=None,
            notes=spec.notes,
        )
        session.add(src)
        session.commit()
        print(f"    pointer source recorded ({spec.notes[:80] if spec.notes else ''}...)")
        return 1, 0

    try:
        local_path = ensure_local(spec.url, spec.local_path)
    except Exception as e:
        print(f"    !! fetch failed ({e}); recording source with note and skipping")
        src = models.Source(
            url=spec.url,
            title=spec.title,
            jurisdiction=spec.jurisdiction,
            department=spec.department,
            source_type=spec.source_type,
            fetched_at=datetime.utcnow(),
            raw_content=None,
            raw_snippet=None,
            local_path=None,
            notes=f"FETCH_FAILED: {e}",
        )
        session.add(src)
        session.commit()
        return 0, 0

    if spec.source_type == "pdf":
        pages = extract_pdf(local_path)
        full_text = all_text(pages)
    else:
        # Not used by current sources — placeholder for HTML pages.
        with open(local_path, "r", encoding="utf-8", errors="ignore") as f:
            full_text = f.read()
        pages = []

    snippet = full_text[:1500] if full_text else None
    note = spec.notes or None
    if spec.source_type == "pdf" and not full_text.strip():
        note = (note + "; " if note else "") + (
            "PDF returned no extractable text (likely scanned image)."
        )

    src = models.Source(
        url=spec.url,
        title=spec.title,
        jurisdiction=spec.jurisdiction,
        department=spec.department,
        source_type=spec.source_type,
        fetched_at=datetime.utcnow(),
        raw_content=full_text or None,
        raw_snippet=snippet,
        local_path=local_path,
        notes=note,
    )
    session.add(src)
    session.flush()  # we need src.id

    parser_fn = PARSERS[spec.parser]
    records: list[FeeRecord] = parser_fn(pages, spec.jurisdiction) if pages else []
    for r in records:
        session.add(models.Fee(
            source_id=src.id,
            fee_name=r.fee_name,
            amount=r.amount,
            amount_text=r.amount_text,
            unit=r.unit,
            department=r.department,
            jurisdiction=r.jurisdiction,
            category=r.category,
            confidence=r.confidence,
            validation_flag=r.validation_flag,
            raw_snippet=r.raw_snippet,
            notes=r.notes,
            extracted_at=datetime.utcnow(),
        ))
    session.commit()
    print(f"    extracted {len(records)} fee records")
    return 1, len(records)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", help="run a single source by key")
    ap.add_argument("--reset", action="store_true", help="drop tables before loading")
    args = ap.parse_args()

    if args.reset:
        Base.metadata.drop_all(bind=engine)
    init_db()

    session = SessionLocal()
    try:
        targets = [s for s in SOURCES if (not args.source or s.key == args.source)]
        if not targets:
            print(f"No source matches '{args.source}'", file=sys.stderr)
            sys.exit(2)

        total_sources = total_fees = 0
        for spec in targets:
            ns, nf = _ingest_source(spec, session)
            total_sources += ns
            total_fees += nf
        print(f"\nDone. Sources stored: {total_sources}. Fees stored: {total_fees}.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
