from datetime import date
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from src.config.database import Base
from src.domain import rules_engine as engine
from src.models.supplement import (
    ChecklistVersion, InspectionLedger, SegmentState, SupplementBatch,
)
from src.services import supplements
from src.services.supplement_io import parse_supplement


@pytest.fixture()
def session_factory(tmp_path):
    # 文件库 + WAL：每个线程独立连接，写事务在 busy_timeout 内排队，
    # 用来验证两批补传交错到达时的串行锁语义。
    db_path = tmp_path / "supplement-test.db"
    engine_db = create_engine(
        f"sqlite:///{db_path}", future=True,
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine_db, "connect")
    def _pragma(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    Base.metadata.create_all(engine_db)
    supplements.ensure_lock_row(engine_db)
    factory = sessionmaker(engine_db, future=True, expire_on_commit=False)
    yield factory
    engine_db.dispose()


def csv(*rows):
    lines = ["inspection_date,route,segment_id,defect,version"]
    for row in rows:
        lines.append(",".join(str(part) for part in row))
    return "\n".join(lines) + "\n"


def ingest(factory, text, key, name="补传批次", params=None):
    with factory() as db:
        batch, created = supplements.ingest(
            db, name, "late.csv", text, key, "analyst", params or {})
        db.commit()
        return batch.id, created


def active_versions(factory):
    with factory() as db:
        return [v.version_no for v in db.scalars(
            select(ChecklistVersion).where(ChecklistVersion.status == 'active'))]


def latest_checklist(factory):
    with factory() as db:
        version = db.scalar(select(ChecklistVersion)
                            .where(ChecklistVersion.status == 'active')
                            .order_by(ChecklistVersion.version_no.desc()).limit(1))
        return version.id, version.version_no, [
            (item.route, item.segment_id, item.consequent) for item in version.items
        ], version.changed_segments


BASE = [
    ("2026-11-23", "北岸步道", "S01", "积水", 1),
    ("2026-11-23", "北岸步道", "S01", "青苔", 1),
    ("2026-11-23", "北岸步道", "S01", "排水口堵塞", 1),
    ("2026-11-23", "北岸步道", "S02", "积水", 1),
    ("2026-11-23", "北岸步道", "S02", "青苔", 1),
    ("2026-11-23", "北岸步道", "S02", "排水口堵塞", 1),
    ("2026-11-23", "北岸步道", "S03", "积水", 1),
    ("2026-11-23", "北岸步道", "S03", "青苔", 1),
    ("2026-11-23", "北岸步道", "S03", "排水口堵塞", 1),
    ("2026-11-23", "北岸步道", "S04", "积水", 1),
    ("2026-11-23", "北岸步道", "S04", "青苔", 1),
    ("2026-11-23", "北岸步道", "S05", "积水", 1),
    ("2026-11-23", "北岸步道", "S09", "排水口堵塞", 1),
    ("2026-11-23", "北岸步道", "S10", "积水", 1),
    ("2026-11-23", "北岸步道", "S10", "青苔", 1),
    ("2026-11-23", "北岸步道", "S10", "路缘破损", 1),
    ("2026-11-23", "观景环线", "S01", "铺装松动", 1),
    ("2026-11-23", "观景环线", "S01", "路缘破损", 1),
    ("2026-11-23", "观景环线", "S02", "铺装松动", 1),
    ("2026-11-23", "观景环线", "S02", "路缘破损", 1),
    ("2026-11-23", "观景环线", "S03", "铺装松动", 1),
    ("2026-11-23", "观景环线", "S03", "路缘破损", 1),
    ("2026-11-23", "观景环线", "S04", "铺装松动", 1),
    ("2026-11-23", "观景环线", "S05", "路缘破损", 1),
]


def test_first_ingest_archives_and_publishes_v1(session_factory):
    bid, created = ingest(session_factory, csv(*BASE), "batch-1")
    assert created is True
    with session_factory() as db:
        ledger_count = db.scalar(select(InspectionLedger).where(InspectionLedger.is_active.is_(True)))
        states = db.scalars(select(SegmentState)).all()
        batch = db.get(SupplementBatch, bid)
        version = db.get(ChecklistVersion, batch.checklist_version_id)
        item_count = len(version.items)
        evidences = [item.evidence for item in version.items]
    assert ledger_count is not None
    assert len(states) == 12
    assert version.version_no == 1 and version.status == 'active'
    assert item_count >= 2
    # 每条加查都带命中依据
    for evidence in evidences:
        assert evidence['support_pct'] is not None and evidence['consequent']


def test_late_record_archived_by_day_and_higher_version_replaces_whole_day(session_factory):
    ingest(session_factory, csv(*BASE), "batch-1")
    # 晚到的同日更正 v2：S04 整份改为只有青苔（去掉积水）
    correction = [
        ("2026-11-23", "北岸步道", "S04", "青苔", 2),
    ]
    bid, _ = ingest(session_factory, csv(*correction), "batch-2")
    with session_factory() as db:
        entries = db.scalars(select(InspectionLedger)
                             .where(InspectionLedger.route == "北岸步道",
                                    InspectionLedger.segment_id == "S04")
                             .order_by(InspectionLedger.version)).all()
        state = db.scalar(select(SegmentState).where(
            SegmentState.route == "北岸步道", SegmentState.segment_id == "S04"))
        batch = db.get(SupplementBatch, bid)
    assert [e.version for e in entries] == [1, 2]
    assert entries[0].is_active is False and entries[1].is_active is True
    assert entries[0].superseded_batch_id == bid
    assert state.defects == ["青苔"] and state.version == 2
    assert batch.replaced_versions == 1
    assert batch.changed_segments == 1  # 只重读 S04 这一个变动路段


def test_same_version_same_content_idempotent_skip_no_new_checklist(session_factory):
    ingest(session_factory, csv(*BASE), "batch-1")
    # 重跑成功批次（同幂等键）：不重复出单
    bid_again, created = ingest(session_factory, csv(*BASE), "batch-1")
    assert created is False
    with session_factory() as db:
        batch_count = db.scalar(select(SupplementBatch.id).limit(2))
        checklist_count = len(db.scalars(select(ChecklistVersion)).all())
        ledger_count = len(db.scalars(select(InspectionLedger)).all())
    assert checklist_count == 1
    assert ledger_count == len({(r[0], r[1], r[2]) for r in BASE})
    # 同版本同内容但新批次：跳过归档，不产生新版本
    bid3, created3 = ingest(session_factory, csv(*BASE), "batch-3")
    assert created3 is True
    with session_factory() as db:
        b3 = db.get(SupplementBatch, bid3)
        checklist_count = len(db.scalars(select(ChecklistVersion)).all())
    assert b3.skipped_versions == len({(r[0], r[1], r[2]) for r in BASE})
    assert checklist_count == 1


def test_same_version_different_content_rejected_and_old_checklist_kept(session_factory):
    ingest(session_factory, csv(*BASE), "batch-1")
    conflicting = [("2026-11-23", "北岸步道", "S04", "积水", 1)]  # v1 但内容不同
    with pytest.raises(ValueError) as info:
        with session_factory() as db:
            supplements.ingest(db, "冲突批次", "x.csv", csv(*conflicting), "batch-2", "analyst", {})
            db.commit()
    assert "版本号" in str(info.value)
    # 失败后旧清单仍 active，且没有残留半截归档
    assert active_versions(session_factory) == [1]
    with session_factory() as db:
        s04 = db.scalar(select(InspectionLedger).where(
            InspectionLedger.route == "北岸步道", InspectionLedger.segment_id == "S04",
            InspectionLedger.is_active.is_(True)))
        states = len(db.scalars(select(SegmentState)).all())
    assert set(s04.defects) == {"积水", "青苔"}
    assert states == 12
    # 失败留痕可重跑
    with session_factory() as db:
        failed = supplements.mark_failed(db, "冲突批次", "x.csv", "batch-2", "analyst", str(info.value))
        db.commit()
        assert failed.status == "failed"


def test_lower_version_late_correction_is_skipped(session_factory):
    ingest(session_factory, csv(*BASE), "batch-1")
    # 先升到 v2
    ingest(session_factory, csv(("2026-11-23", "北岸步道", "S05", "青苔", 2)), "batch-2")
    # 再晚到 v1 的更正（低版本），应跳过
    bid, _ = ingest(session_factory, csv(("2026-11-23", "北岸步道", "S05", "积水", 1)), "batch-3")
    with session_factory() as db:
        batch = db.get(SupplementBatch, bid)
        state = db.scalar(select(SegmentState).where(
            SegmentState.route == "北岸步道", SegmentState.segment_id == "S05"))
    assert batch.skipped_versions == 1
    assert state.version == 2 and state.defects == ["青苔"]


def test_clean_segment_entering_denominator_shifts_other_segments_checks(session_factory):
    # 受控数据：10 个路段中 3 段同时有积水、青苔，其中 2 段还有排水口堵塞；
    # 另有一段只有积水+青苔，用于承接规则命中的加查项。
    controlled = [
        ("2026-11-23", "北岸步道", "S01", "积水", 1),
        ("2026-11-23", "北岸步道", "S01", "青苔", 1),
        ("2026-11-23", "北岸步道", "S01", "排水口堵塞", 1),
        ("2026-11-23", "北岸步道", "S02", "积水", 1),
        ("2026-11-23", "北岸步道", "S02", "青苔", 1),
        ("2026-11-23", "北岸步道", "S02", "排水口堵塞", 1),
        ("2026-11-23", "北岸步道", "S03", "积水", 1),
        ("2026-11-23", "北岸步道", "S03", "青苔", 1),
        *(("2026-11-23", "北岸步道", f"S{n:02d}", "护栏松动", 1) for n in range(4, 11)),
    ]
    params = {"route_scope": "北岸步道", "min_support": 0.15,
              "min_confidence": 0.6, "min_lift": 1.05}
    ingest(session_factory, csv(*controlled), "batch-1", params=params)
    _, v1_no, items_v1, _ = latest_checklist(session_factory)
    # 支持度 2/10=20% 达标，S03 命中 积水+青苔 -> 排水口堵塞 的加查
    assert ("北岸步道", "S03", "排水口堵塞") in items_v1

    # 补传 4 个无缺陷路段：分母 10 -> 14，支持度 2/14≈14.3% 跌破 15%
    clean_batch = [(f"2026-11-24", "北岸步道", f"S{n:02d}", "无缺陷", 1) for n in range(11, 15)]
    ingest(session_factory, csv(*clean_batch), "batch-clean", params=params)
    _, v2_no, items_v2, changed = latest_checklist(session_factory)
    # 清单整体切换到新版本，旧版被标记 superseded
    assert v2_no == v1_no + 1
    assert active_versions(session_factory) == [v2_no]
    # 变动集合只包含四个真正重读的路段
    assert sorted(changed) == sorted(["北岸步道", f"S{n:02d}"] for n in range(11, 15))
    # 规则因分母变化退出达标，S03 的加查被撤销，其它路段也未新增该项
    assert all(item[2] != "排水口堵塞" for item in items_v2)
    assert ("北岸步道", "S03", "排水口堵塞") not in items_v2
    assert len(items_v2) < len(items_v1)


def test_newer_day_supersedes_older_record(session_factory):
    ingest(session_factory, csv(*BASE), "batch-1")
    # S09 新一天的无缺陷记录，覆盖旧的排水口堵塞
    ingest(session_factory, csv(("2026-11-24", "北岸步道", "S09", "无缺陷", 1)), "batch-new-day")
    with session_factory() as db:
        state = db.scalar(select(SegmentState).where(
            SegmentState.route == "北岸步道", SegmentState.segment_id == "S09"))
        ledgers = db.scalars(select(InspectionLedger).where(
            InspectionLedger.route == "北岸步道", InspectionLedger.segment_id == "S09")).all()
    assert state.inspection_date == date(2026, 11, 24)
    assert state.defects == []
    assert {e.is_active for e in ledgers} == {True, True}  # 不同日期都保留，状态取最新日


def test_failed_recompute_rolls_back_everything(session_factory):
    ingest(session_factory, csv(*BASE), "batch-1")
    # 构造一个无法通过同日一致性校验的批次
    bad = [
        ("2026-11-24", "北岸步道", "S08", "无缺陷", 1),
        ("2026-11-24", "北岸步道", "S08", "积水", 1),
    ]
    with pytest.raises(ValueError):
        with session_factory() as db:
            supplements.ingest(db, "坏批次", "bad.csv", csv(*bad), "batch-bad", "analyst", {})
            db.commit()
    assert active_versions(session_factory) == [1]
    with session_factory() as db:
        assert not db.scalars(select(InspectionLedger).where(
            InspectionLedger.route == "北岸步道", InspectionLedger.segment_id == "S08")).first()
        assert db.scalar(select(SegmentState).where(
            SegmentState.route == "北岸步道", SegmentState.segment_id == "S08")) is None
        assert len(db.scalars(select(SegmentState)).all()) == 12


def test_two_interleaved_batches_both_processed_without_loss(session_factory):
    batch_a = csv(("2026-11-24", "北岸步道", "S08", "无缺陷", 1))
    batch_b = csv(("2026-11-24", "观景环线", "S08", "标识倒伏", 1),
                  ("2026-11-24", "观景环线", "S08", "护栏松动", 1))

    def run(text, key):
        with session_factory() as db:
            batch, _ = supplements.ingest(db, key, "i.csv", text, key, "analyst", {})
            db.commit()
            return batch.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda args: run(*args), [(batch_a, "key-a"), (batch_b, "key-b")]))
    assert len(set(results)) == 2
    with session_factory() as db:
        states = {(s.route, s.segment_id): s for s in db.scalars(select(SegmentState)).all()}
        batches = db.scalars(select(SupplementBatch).order_by(SupplementBatch.id)).all()
        versions = db.scalars(select(ChecklistVersion).order_by(ChecklistVersion.version_no)).all()
    assert states[("北岸步道", "S08")].defects == []
    assert set(states[("观景环线", "S08")].defects) == {"标识倒伏", "护栏松动"}
    assert {b.status for b in batches} == {"complete"}
    assert [v.status for v in versions] == ["superseded", "active"]
    assert versions[-1].version_no == 2


def test_scope_param_isolated_checklist(session_factory):
    ingest(session_factory, csv(*BASE), "batch-1",
           params={"route_scope": "北岸步道"})
    _, _, items, _ = latest_checklist(session_factory)
    assert all(route == "北岸步道" for route, _, _ in items)


def test_parse_supplement_accepts_legacy_four_columns():
    text = "inspection_date,route,segment_id,defect\n2026-11-24,北岸步道,S08,无缺陷\n"
    rows = parse_supplement(text)
    assert rows == [{
        "inspection_date": "2026-11-24", "route": "北岸步道", "segment_id": "S08",
        "defect": "无缺陷", "version": 1,
    }]


def test_parse_supplement_rejects_bad_header():
    with pytest.raises(ValueError):
        parse_supplement("date,route,segment_id,defect\n2026-11-24,a,b,无缺陷\n")
