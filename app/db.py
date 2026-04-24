import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_PATH = os.environ.get("FEES_DB_PATH", os.path.join(PROJECT_ROOT, "data", "fees.db"))
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, future=True, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    from app import models  # noqa: F401  ensures model registration

    Base.metadata.create_all(bind=engine)
