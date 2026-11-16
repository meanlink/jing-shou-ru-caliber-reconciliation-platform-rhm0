from sqlalchemy import String, JSON, Double, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class MetricDefinition(Base):
    """A named financial metric (口径). Codes are stable reference keys."""
    __tablename__ = 'metric_definitions'
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(String(500), default='')
    status: Mapped[str] = mapped_column(String(20), default='active')
    current_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    updated_at: Mapped[float] = mapped_column(Double)


class MetricVersion(Base):
    """Immutable published snapshot. Dependencies pin exact version ids."""
    __tablename__ = 'metric_versions'
    __table_args__ = (UniqueConstraint('metric_id', 'version_no', name='uq_metric_version_no'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    metric_id: Mapped[int] = mapped_column(ForeignKey('metric_definitions.id'), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    expression: Mapped[str] = mapped_column(String(300))
    unit: Mapped[str] = mapped_column(String(40))
    dependencies: Mapped[list] = mapped_column(JSON, default=list)
    resolved: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    published_at: Mapped[float] = mapped_column(Double)
    note: Mapped[str] = mapped_column(String(500), default='')


class MetricDraft(Base):
    """One editable draft per metric. Never used directly by already-bound jobs."""
    __tablename__ = 'metric_drafts'
    id: Mapped[int] = mapped_column(primary_key=True)
    metric_id: Mapped[int] = mapped_column(ForeignKey('metric_definitions.id'), unique=True)
    expression: Mapped[str] = mapped_column(String(300))
    base_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(String(500), default='')
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    updated_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    updated_at: Mapped[float] = mapped_column(Double)
