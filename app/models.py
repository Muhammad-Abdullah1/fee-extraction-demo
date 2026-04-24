from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Float,
    DateTime,
    ForeignKey,
    Index,
)
from sqlalchemy.orm import relationship

from app.db import Base


class Source(Base):
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True)
    url = Column(String, nullable=False)
    title = Column(String, nullable=False)
    jurisdiction = Column(String, nullable=False)
    department = Column(String, nullable=True)
    source_type = Column(String, nullable=False)  # 'pdf' | 'html'
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    raw_content = Column(Text, nullable=True)   # full extracted text
    raw_snippet = Column(Text, nullable=True)   # short representative snippet
    local_path = Column(String, nullable=True)  # cached file location
    notes = Column(Text, nullable=True)

    fees = relationship("Fee", back_populates="source", cascade="all, delete-orphan")


class Fee(Base):
    __tablename__ = "fees"

    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=False)
    fee_name = Column(String, nullable=False)
    amount = Column(Float, nullable=True)        # numeric amount when parseable
    amount_text = Column(String, nullable=False) # the literal text seen ("$ 223", "3.75%")
    unit = Column(String, nullable=True)         # "Per System", "Each", "Per Page"...
    department = Column(String, nullable=True)
    jurisdiction = Column(String, nullable=False)
    category = Column(String, nullable=True)     # Water | Environmental Health | Fire | Land Use | Development
    confidence = Column(Float, nullable=False, default=0.0)
    validation_flag = Column(String, nullable=False, default="needs_review")
    raw_snippet = Column(Text, nullable=False)   # the exact line/cell this came from
    notes = Column(Text, nullable=True)
    extracted_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    source = relationship("Source", back_populates="fees")


Index("ix_fees_source_name", Fee.source_id, Fee.fee_name)


class ChangeLog(Base):
    __tablename__ = "change_logs"

    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=False)
    fee_id = Column(Integer, ForeignKey("fees.id"), nullable=True)
    field_name = Column(String, nullable=False)
    old_value = Column(String, nullable=True)
    new_value = Column(String, nullable=True)
    detected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
