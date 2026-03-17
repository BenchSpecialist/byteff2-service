import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    String,
    Float,
    Integer,
    Enum as SAEnum,
    DateTime,
    ForeignKey,
    Text,
    Index,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class JobStatusEnum(enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


def _utcnow():
    return datetime.now(timezone.utc)


class Formulation(Base):
    __tablename__ = "formulations"

    uid = Column(String, primary_key=True)
    formulation_id = Column(String, nullable=False)
    created_at = Column(DateTime, default=_utcnow)

    components = relationship(
        "Component", back_populates="formulation", cascade="all, delete-orphan"
    )
    job = relationship(
        "Job", back_populates="formulation", uselist=False, cascade="all, delete-orphan"
    )


class Component(Base):
    __tablename__ = "components"

    id = Column(Integer, primary_key=True, autoincrement=True)
    formulation_uid = Column(String, ForeignKey("formulations.uid"), nullable=False)
    component_id = Column(String, nullable=False)  # "Solvent", "Salt", "Additive"
    name = Column(String, nullable=False)  # "EC", "LiPF6", etc.
    weight_fraction = Column(Float, nullable=False)

    formulation = relationship("Formulation", back_populates="components")


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    formulation_uid = Column(
        String, ForeignKey("formulations.uid"), nullable=False, unique=True
    )
    status = Column(
        SAEnum(JobStatusEnum), nullable=False, default=JobStatusEnum.PENDING
    )
    progress_pct = Column(Float, default=0.0)
    stage_name = Column(String, nullable=True)
    message = Column(Text, nullable=True)
    k8s_pod_name = Column(String, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    formulation = relationship("Formulation", back_populates="job")

    __table_args__ = (Index("ix_jobs_status", "status"),)
