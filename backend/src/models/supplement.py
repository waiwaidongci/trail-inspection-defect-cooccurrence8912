"""补传巡检台账模型。

- SupplementBatch：补传批次，按幂等键去重，重跑同一批不会重复出单。
- InspectionLedger：晚到记录按（步道、路段、巡检日期）归档，同日更正只保留最高版本整份。
- SegmentState：每个（步道、路段）当前生效的巡检，增量试算只重读有变动的路段。
- ChecklistVersion：下一班加查清单的版本，清单与命中依据同版整体切换，失败保留旧版。
- ChecklistItem：清单内逐段加查项及其命中依据（规则）。
"""
from datetime import date, datetime
from sqlalchemy import JSON, Date, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.config.database import Base
from src.utils.time import now


class IngestLock(Base):
    """单行锁：保证两批补传即使交错到达，也按顺序完整处理、不漏批不串批。"""
    __tablename__ = 'ingest_lock'

    id: Mapped[int] = mapped_column(primary_key=True)
    holder: Mapped[str | None] = mapped_column(String(80))
    taken_at: Mapped[datetime | None]


class SupplementBatch(Base):
    __tablename__ = 'supplement_batches'
    __table_args__ = (UniqueConstraint('idempotency_key', name='uq_supplement_batch_key'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    filename: Mapped[str] = mapped_column(String(160))
    idempotency_key: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default='processing', index=True)
    row_count: Mapped[int] = mapped_column(default=0)
    changed_segments: Mapped[int] = mapped_column(default=0)
    replaced_versions: Mapped[int] = mapped_column(default=0)
    skipped_versions: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(String(500))
    checklist_version_id: Mapped[int | None] = mapped_column(ForeignKey('checklist_versions.id'))
    author: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=now)
    finished_at: Mapped[datetime | None]


class InspectionLedger(Base):
    __tablename__ = 'inspection_ledgers'
    __table_args__ = (
        UniqueConstraint('route', 'segment_id', 'inspection_date', 'version',
                         name='uq_inspection_ledger_day_version'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    route: Mapped[str] = mapped_column(String(120), index=True)
    segment_id: Mapped[str] = mapped_column(String(60), index=True)
    inspection_date: Mapped[date] = mapped_column(Date, index=True)
    version: Mapped[int] = mapped_column(Integer)
    defects: Mapped[list] = mapped_column(JSON)
    batch_id: Mapped[int] = mapped_column(ForeignKey('supplement_batches.id'))
    superseded_batch_id: Mapped[int | None] = mapped_column(ForeignKey('supplement_batches.id'))
    is_active: Mapped[bool] = mapped_column(default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(default=now)


class SegmentState(Base):
    __tablename__ = 'segment_states'
    __table_args__ = (
        UniqueConstraint('route', 'segment_id', name='uq_segment_state_segment'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    route: Mapped[str] = mapped_column(String(120), index=True)
    segment_id: Mapped[str] = mapped_column(String(60), index=True)
    inspection_date: Mapped[date] = mapped_column(Date)
    defects: Mapped[list] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer)
    ledger_id: Mapped[int] = mapped_column(ForeignKey('inspection_ledgers.id'))
    batch_id: Mapped[int] = mapped_column(ForeignKey('supplement_batches.id'))
    updated_at: Mapped[datetime] = mapped_column(default=now, onupdate=now)


class ChecklistVersion(Base):
    __tablename__ = 'checklist_versions'

    id: Mapped[int] = mapped_column(primary_key=True)
    version_no: Mapped[int] = mapped_column(Integer, unique=True)
    status: Mapped[str] = mapped_column(String(20), default='active', index=True)
    scope: Mapped[str] = mapped_column(String(120))
    params: Mapped[dict] = mapped_column(JSON)
    total_segments: Mapped[int] = mapped_column(Integer)
    clean_segments: Mapped[int] = mapped_column(Integer)
    rule_count: Mapped[int] = mapped_column(Integer)
    changed_segments: Mapped[list] = mapped_column(JSON)
    summary: Mapped[str] = mapped_column(String(500))
    rules_snapshot: Mapped[list] = mapped_column(JSON)
    trigger_batch_id: Mapped[int | None] = mapped_column(ForeignKey('supplement_batches.id'))
    author: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=now)

    items: Mapped[list['ChecklistItem']] = relationship(
        back_populates='checklist', cascade='all, delete-orphan'
    )


class ChecklistItem(Base):
    __tablename__ = 'checklist_items'

    id: Mapped[int] = mapped_column(primary_key=True)
    checklist_version_id: Mapped[int] = mapped_column(
        ForeignKey('checklist_versions.id'), index=True
    )
    route: Mapped[str] = mapped_column(String(120), index=True)
    segment_id: Mapped[str] = mapped_column(String(60), index=True)
    inspection_date: Mapped[date] = mapped_column(Date)
    antecedent: Mapped[str] = mapped_column(String(200))
    consequent: Mapped[str] = mapped_column(String(60))
    action: Mapped[str] = mapped_column(String(300))
    evidence: Mapped[dict] = mapped_column(JSON)

    checklist: Mapped[ChecklistVersion] = relationship(back_populates='items')
