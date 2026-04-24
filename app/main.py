"""FastAPI app — minimal HTML demo.

Pages:
    /                  — fees table with filters
    /fees/{id}         — single fee with raw source snippet
    /sources           — list of ingested sources
    /sources/{id}      — single source detail (raw extracted text)
"""
from __future__ import annotations

import os
from typing import Optional

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
