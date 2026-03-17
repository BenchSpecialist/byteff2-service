import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from job_scheduler.db.models import Base

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///jobcli.db")

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)


def init_db():
    """Create all tables if they do not exist."""
    Base.metadata.create_all(engine)
