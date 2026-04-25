"""FastAPI app — minimal HTML demo.

Pages:
    /                  — fees table with filters
    /fees/{id}         — single fee with raw source snippet
    /sources           — list of ingested sources
    /sources/{id}      — single source detail (raw extracted text)
    /estimate          — interactive estimate cart builder
    /api/estimate-fees — JSON fee items for estimate builder
"""
from __future__ import annotations

import os
import re
from typing import Optional, Any

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_

from app.db import SessionLocal, init_db
from app import models

BASE_DIR = os.path.dirname(__file__)

app = FastAPI(title="Fee Extraction Demo")
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


@app.on_event("startup")
def _startup():
    init_db()


def _summary_counts(session):
    from sqlalchemy import func
    total = session.query(func.count(models.Fee.id)).scalar() or 0
    by_flag = dict(
        session.query(models.Fee.validation_flag, func.count(models.Fee.id))
        .group_by(models.Fee.validation_flag).all()
    )
    by_category = dict(
        session.query(models.Fee.category, func.count(models.Fee.id))
        .group_by(models.Fee.category).all()
    )
    sources = session.query(func.count(models.Source.id)).scalar() or 0
    return {
        "total": total,
        "verified": by_flag.get("verified", 0),
        "needs_review": by_flag.get("needs_review", 0),
        "conflicting": by_flag.get("conflicting", 0),
        "incomplete": by_flag.get("incomplete", 0),
        "by_category": by_category,
        "sources": sources,
    }


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    q: Optional[str] = None,
    category: Optional[str] = None,
    jurisdiction: Optional[str] = None,
    flag: Optional[str] = None,
    limit: int = 200,
):
    session = SessionLocal()
    try:
        query = session.query(models.Fee).join(models.Source, models.Fee.source_id == models.Source.id)
        if q:
            qx = f"%{q}%"
            query = query.filter(or_(
                models.Fee.fee_name.ilike(qx),
                models.Fee.raw_snippet.ilike(qx),
            ))
        if category:
            query = query.filter(models.Fee.category == category)
        if jurisdiction:
            query = query.filter(models.Fee.jurisdiction == jurisdiction)
        if flag:
            query = query.filter(models.Fee.validation_flag == flag)

        total_matching = query.count()
        fees = query.order_by(
            models.Fee.jurisdiction, models.Fee.category, models.Fee.fee_name
        ).limit(limit).all()

        # Pre-load sources keyed by id (small set)
        src_ids = {f.source_id for f in fees}
        sources = {s.id: s for s in session.query(models.Source).filter(models.Source.id.in_(src_ids)).all()} if src_ids else {}

        categories = sorted({c for (c,) in session.query(models.Fee.category).distinct() if c})
        jurisdictions = sorted({j for (j,) in session.query(models.Fee.jurisdiction).distinct() if j})

        return templates.TemplateResponse("index.html", {
            "request": request,
            "fees": fees,
            "sources": sources,
            "summary": _summary_counts(session),
            "categories": categories,
            "jurisdictions": jurisdictions,
            "filters": {
                "q": q or "",
                "category": category or "",
                "jurisdiction": jurisdiction or "",
                "flag": flag or "",
                "limit": limit,
            },
            "total_matching": total_matching,
        })
    finally:
        session.close()


@app.get("/fees/{fee_id}", response_class=HTMLResponse)
def fee_detail(request: Request, fee_id: int):
    session = SessionLocal()
    try:
        fee = session.get(models.Fee, fee_id)
        if not fee:
            raise HTTPException(404, "fee not found")
        source = session.get(models.Source, fee.source_id)
        return templates.TemplateResponse("fee.html", {
            "request": request,
            "fee": fee,
            "source": source,
        })
    finally:
        session.close()


@app.get("/sources", response_class=HTMLResponse)
def sources_index(request: Request):
    session = SessionLocal()
    try:
        from sqlalchemy import func
        rows = (
            session.query(models.Source, func.count(models.Fee.id))
            .outerjoin(models.Fee, models.Fee.source_id == models.Source.id)
            .group_by(models.Source.id)
            .order_by(models.Source.id)
            .all()
        )
        return templates.TemplateResponse("sources.html", {
            "request": request,
            "rows": rows,
            "summary": _summary_counts(session),
        })
    finally:
        session.close()


@app.get("/sources/{source_id}", response_class=HTMLResponse)
def source_detail(request: Request, source_id: int):
    session = SessionLocal()
    try:
        src = session.get(models.Source, source_id)
        if not src:
            raise HTTPException(404, "source not found")
        fees = (
            session.query(models.Fee)
            .filter(models.Fee.source_id == source_id)
            .order_by(models.Fee.fee_name)
            .all()
        )
        return templates.TemplateResponse("source.html", {
            "request": request,
            "source": src,
            "fees": fees,
        })
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Estimate builder
# ---------------------------------------------------------------------------

def _item(
    key: str,
    name: str,
    amount: Optional[float],
    amount_text: str,
    status: str,
    source_label: str,
    source_url: Optional[str] = None,
    fee_id: Optional[int] = None,
    category: str = "Development fees",
    department: str = "",
    notes: str = "",
    outreach: Optional[dict] = None,
    included: bool = True,
) -> dict[str, Any]:
    return {
        "key": key,
        "fee_name": name,
        "amount": amount,
        "amount_text": amount_text,
        "status": status,
        "source_label": source_label,
        "source_url": source_url,
        "fee_id": fee_id,
        "category": category,
        "department": department,
        "notes": notes,
        "outreach": outreach,
        "included": included,
    }


def _build_calimesa_items(session, sqft: float, project_type: str) -> list:
    items = []
    is_replacement = project_type == "replacement_mh"

    school_rate = 4.79
    school_amount = round(sqft * school_rate, 2)
    items.append(_item(
        "school_fees", "School Fees",
        school_amount, f"${school_amount:,.2f}", "calculated",
        f"Yucaipa-Calimesa Joint USD × ${school_rate}/sq ft",
        source_url="https://www.ycjusd.us/",
        category="Development fees", department="School District",
        notes=f"Calculated: {sqft:,.0f} sq ft × ${school_rate}/sq ft. Verify rate with district at time of permit.",
        outreach={"name": "Yucaipa-Calimesa Joint USD", "phone": "(909) 797-0174", "url": "https://www.ycjusd.us/"},
    ))

    cal_dif_src = session.query(models.Source).filter(models.Source.title.like("%Calimesa Development Impact%")).first()
    if cal_dif_src:
        dif_fees = (
            session.query(models.Fee)
            .filter(
                models.Fee.source_id == cal_dif_src.id,
                models.Fee.fee_name.like("%Mobile Home%"),
                models.Fee.fee_name.notlike("%Total%"),
                models.Fee.amount.isnot(None),
            )
            .order_by(models.Fee.fee_name)
            .all()
        )
        for f in dif_fees:
            component = f.fee_name.split("—")[-1].strip()
            items.append(_item(
                f"cal_dif_{f.id}", f"DIF — {component}",
                f.amount, f.amount_text, "needs_review",
                "Calimesa DIF Schedule (Res. 2017-11) — OCR-derived, verify original",
                source_url=cal_dif_src.url, fee_id=f.id,
                category=f.category, department="City of Calimesa",
                notes=f"Mobile Home rate (OCR-derived — verify against original document). {f.notes or ''}".strip(),
                included=not is_replacement,
            ))

    cal_master_src = session.query(models.Source).filter(models.Source.title.like("%Calimesa Master%")).first()
    if cal_master_src:
        pc = (
            session.query(models.Fee)
            .filter(
                models.Fee.source_id == cal_master_src.id,
                models.Fee.department == "Building",
                models.Fee.fee_name.ilike("%plan check%"),
                models.Fee.amount.isnot(None),
            )
            .first()
        )
        if pc:
            items.append(_item(
                "plan_check", "Plan Check Fee",
                pc.amount, pc.amount_text, "verified",
                "City of Calimesa Master Fee Schedule (Sept 2025)",
                source_url=cal_master_src.url, fee_id=pc.id,
                category="Development fees", department="Building",
                notes="Base rate shown. Final fee scales with project valuation — contact Building Dept for exact amount.",
                outreach={"name": "Calimesa Building & Safety", "phone": "(909) 795-9801", "url": "https://www.calimesa.gov/185/Building-Safety"},
            ))

        fire_fees = (
            session.query(models.Fee)
            .filter(
                models.Fee.source_id == cal_master_src.id,
                models.Fee.category == "Fire Department",
                models.Fee.fee_name.ilike("%3 dwelling%"),
                models.Fee.amount.isnot(None),
            )
            .first()
        )
        if fire_fees:
            items.append(_item(
                "fire_plan_check", "Fire Department Plan Check / Inspection",
                fire_fees.amount, fire_fees.amount_text, "verified",
                "City of Calimesa Master Fee Schedule (Sept 2025) — Fire Dept",
                source_url=cal_master_src.url, fee_id=fire_fees.id,
                category="Fire Department", department="Fire Department",
                notes="Single dwelling unit base rate. Verify current rate with Calimesa Fire for your specific project.",
                outreach={"name": "Calimesa Fire Department", "phone": "(909) 795-9801", "url": "https://www.calimesa.gov/"},
            ))

    items.append(_item(
        "fire_flow_test", "Fire Flow Test",
        600.0, "$600", "approximate",
        "Water district — typical rate based on recent Calimesa projects",
        category="Fire Department", department="Water District / Fire Dept",
        notes="Contact water provider to schedule. Typically $450–$600. Required before permit issuance.",
        outreach={"name": "Calimesa Building & Safety", "phone": "(909) 795-9801", "url": "https://www.calimesa.gov/185/Building-Safety"},
    ))

    items.append(_item(
        "sewer_connection", "Sewer Connection Fee",
        None, "TBD", "tbd",
        "Utility district — contact for current rates",
        category="Development fees", department="Wastewater Utility",
        notes="Contact local wastewater district for connection fees and capacity charges. Varies by district and service level.",
        outreach={"name": "Calimesa Public Works", "phone": "(909) 795-9801", "url": "https://www.calimesa.gov/"},
        included=False,
    ))

    items.append(_item(
        "water_meter_yvwd", "Water Meter / Connection (YVWD)",
        None, "TBD", "tbd",
        "Yucaipa Valley Water District — no public rate schedule found online",
        source_url="https://www.yvwd.us/",
        category="Water", department="Yucaipa Valley Water District",
        notes="YVWD serves Calimesa. No consolidated public rate PDF located. Contact YVWD directly for meter upgrade and connection fees.",
        outreach={"name": "Yucaipa Valley Water District", "phone": "(909) 797-5117", "url": "https://www.yvwd.us/"},
        included=False,
    ))

    items.append(_item(
        "edison", "Edison / SCE Costs",
        5000.0, "~$5,000", "approximate",
        "SCE — contingency estimate (cost determined after plans submitted)",
        category="Development fees", department="Southern California Edison",
        notes="Final cost determined after plans are submitted to SCE. Approximated contingency fund.",
        outreach={"name": "Southern California Edison", "phone": "1-800-990-7788", "url": "https://www.sce.com/"},
    ))

    items.append(_item(
        "fire_sprinklers", "Fire Sprinklers",
        None, "TBD", "tbd",
        "Factory — required by state for new manufactured home",
        category="Fire Department", department="Home Dealer / Factory",
        notes="Required by factory for new manufactured home per California state code. TBD by customer and home dealer.",
        included=False,
    ))

    return items


def _build_rivco_items(session, sqft: float, project_type: str, area_plan: str) -> list:
    items = []
    is_replacement = project_type == "replacement_mh"

    school_rate = 4.79
    school_amount = round(sqft * school_rate, 2)
    items.append(_item(
        "school_fees", "School Fees",
        school_amount, f"${school_amount:,.2f}", "calculated",
        f"Applicable school district × ${school_rate}/sq ft",
        category="Development fees", department="School District",
        notes=f"Calculated: {sqft:,.0f} sq ft × ${school_rate}/sq ft. Identify applicable district and verify current rate at time of permit.",
        outreach={"name": "Riverside County Schools Info", "phone": "(951) 826-6530", "url": "https://www.rctlma.org/"},
    ))

    dif_src = session.query(models.Source).filter(models.Source.title.like("%659%")).first()
    if dif_src and area_plan:
        dif_fees = (
            session.query(models.Fee)
            .filter(
                models.Fee.source_id == dif_src.id,
                models.Fee.fee_name.like(f"DIF — {area_plan} — Residential — %"),
                models.Fee.fee_name.notlike("% — Total"),
                models.Fee.amount.isnot(None),
                models.Fee.amount > 0,
            )
            .order_by(models.Fee.fee_name)
            .all()
        )
        for f in dif_fees:
            component = f.fee_name.split("—")[-1].strip()
            calc = round(f.amount * sqft, 2)
            items.append(_item(
                f"rivco_dif_{f.id}", f"Riverside County DIF — {component}",
                calc, f"${calc:,.2f}", f.validation_flag,
                f"Rivco DIF Ord. 659.14 — {area_plan}, Residential",
                source_url=dif_src.url, fee_id=f.id,
                category=f.category, department="Riverside County",
                notes=f"Rate: {f.amount_text}/{f.unit or 'sq ft'} × {sqft:,.0f} sq ft. Area plan: {area_plan}.",
                included=not is_replacement,
            ))

    items.append(_item(
        "tumf", "TUMF Fee",
        2740.0, "$2,740", "approximate",
        "WRCOG — Transportation Uniform Mitigation Fee (approx., based on recent projects)",
        source_url="https://www.wrcog.us/",
        category="Land Use Management", department="WRCOG",
        notes="Collected by WRCOG. May be EXEMPT for replacement homes. Contact WRCOG for current rate and exemption status.",
        outreach={"name": "WRCOG", "phone": "(951) 405-6700", "url": "https://www.wrcog.us/"},
        included=not is_replacement,
    ))

    items.append(_item(
        "open_space", "Open Space Preservation Fee",
        1625.0, "$1,625", "approximate",
        "Riverside County Development Fees (approximate)",
        category="Land Use Management", department="Riverside County",
        notes="May apply conditionally. If home removed for >1 year: ~$4,236. Verify with Riverside County Planning.",
        outreach={"name": "Riverside County Planning", "phone": "(951) 955-3200", "url": "https://planning.rctlma.org/"},
    ))

    eh_src = session.query(models.Source).filter(models.Source.title.like("%Environmental Health%")).first()
    items.append(_item(
        "fire_flow_test", "Fire Hydrant Flow Test",
        450.0, "$450", "approximate",
        "Water district — typical rate for Riverside County",
        source_url=eh_src.url if eh_src else None,
        category="Fire Department", department="Water District",
        notes="Completed by local water district. Confirm rate with your provider before permit submittal.",
        outreach={"name": "Local Water District", "phone": "Varies by area", "url": "https://www.rivcoeh.org/"},
    ))

    items.append(_item(
        "perc_test", "Perc Test / Septic Design",
        4000.0, "~$4,000", "approximate",
        "Riverside County soils engineer — estimated range",
        source_url="https://www.rivcoeh.org/",
        category="Environmental Health", department="Soils Engineer / Riverside County DEH",
        notes="Required if no public sewer connection available. Arranged with county-approved soils engineer. Approximate — final cost varies.",
        outreach={"name": "Riverside County DEH", "phone": "(951) 461-0284", "url": "https://www.rivcoeh.org/"},
        included=project_type == "new_mh",
    ))

    items.append(_item(
        "water_meter", "Water Meter / Service Upgrade",
        None, "Pending", "tbd",
        "Water district — pending response from engineering",
        category="Water", department="Water District",
        notes="Contact local water district for meter upgrade and service connection costs. Required before final inspection.",
        outreach={"name": "Local Water District Engineering", "phone": "Contact varies by service area", "url": ""},
        included=False,
    ))

    items.append(_item(
        "edison", "Edison / SCE Costs",
        5000.0, "~$5,000", "approximate",
        "SCE — contingency estimate before plans submitted",
        category="Development fees", department="Southern California Edison",
        notes="Final cost determined after plans submitted to SCE. Approximated contingency fund — may increase for longer runs.",
        outreach={"name": "Southern California Edison", "phone": "1-800-990-7788", "url": "https://www.sce.com/"},
    ))

    items.append(_item(
        "fire_sprinklers", "Fire Sprinklers",
        None, "TBD", "tbd",
        "Factory — required by CA state code for new manufactured homes",
        category="Fire Department", department="Home Dealer / Factory",
        notes="Required for new manufactured home per state code. If in Fire Hazard Zone, additional requirements may apply. TBD by customer and home dealer.",
        included=False,
    ))

    return items


@app.get("/estimate", response_class=HTMLResponse)
def estimate_builder(request: Request):
    session = SessionLocal()
    try:
        area_plans = sorted({
            m.group(1)
            for (fn,) in session.query(models.Fee.fee_name)
            .filter(models.Fee.fee_name.like("DIF — % — Residential — Total"))
            .distinct()
            .all()
            if (m := re.match(r"DIF — (.+?) — Residential — Total", fn))
        })
        return templates.TemplateResponse("estimate.html", {
            "request": request,
            "area_plans": area_plans,
        })
    finally:
        session.close()


@app.get("/api/estimate-fees")
def api_estimate_fees(
    jurisdiction: str = "calimesa",
    sqft: float = 1248,
    project_type: str = "new_mh",
    area_plan: str = "Coachella - Western (AP 2)",
):
    session = SessionLocal()
    try:
        if jurisdiction == "calimesa":
            items = _build_calimesa_items(session, sqft, project_type)
        else:
            items = _build_rivco_items(session, sqft, project_type, area_plan)
        return {"items": items, "jurisdiction": jurisdiction, "sqft": sqft}
    finally:
        session.close()
