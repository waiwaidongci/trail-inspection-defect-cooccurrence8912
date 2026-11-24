"""补传巡检归档与增量试算服务。

关键约定：

1. 晚到记录按（步道、路段、巡检日期）归档进 inspection_ledgers，带版本号；
   同日更正只有更高版本能整份替换当前有效版本，同版本同内容幂等跳过，
   同版本不同内容拒绝（要求提高版本号），低版本视为过期更正直接跳过。
2. segment_states 保存每个路段当前生效（最近巡检日）的记录；只有它实际
   变化的路段才进入本次变动集合——试算只重读这些路段的原始记录，其余
   路段直接沿用状态投影。
3. 规则与清单在状态投影上整体重算：无缺陷路段进出分母后，所有路段的
   达标判断与加查增撤由同一引擎自动联动。
4. 新清单（含逐段命中依据）与旧版切换在同一事务内完成；任何一步失败
   整体回滚，旧版清单原样保留。
5. 补传批次以幂等键去重，重跑成功批次不重复出单；单行串行锁保证两批
   交错到达时按顺序完整处理，不漏批、不串批。
"""
import hashlib
from collections import defaultdict
from datetime import date

from sqlalchemy import select

from src.domain import calculator
from src.domain import rules_engine as engine
from src.models.supplement import (
    ChecklistItem, ChecklistVersion, IngestLock, InspectionLedger,
    SegmentState, SupplementBatch,
)
from src.services.audit import record
from src.services.supplement_io import parse_supplement
from src.utils.time import now


def _key(route, segment_id, day):
    return route, segment_id, day


def _state_key(route, segment_id):
    return route, segment_id


def ensure_lock_row(bind):
    """建表后预置单行锁（幂等），运行期只做行级加锁，不再内嵌写事务。"""
    from sqlalchemy import text
    with bind.begin() as connection:
        connection.execute(
            text("INSERT INTO ingest_lock (id, holder, taken_at) "
                 "SELECT 1, NULL, NULL WHERE NOT EXISTS (SELECT 1 FROM ingest_lock WHERE id = 1)")
        )


def acquire_lock(db):
    """获取补传串行锁；PostgreSQL 为行级写锁，SQLite 退化为库级写串行。"""
    lock = db.get(IngestLock, 1, with_for_update=True)
    if lock is None:  # 未走启动建表路径时的兜底
        ensure_lock_row(db.bind)
        lock = db.get(IngestLock, 1, with_for_update=True)
    return lock


def _group_rows(rows):
    """把一个批次的行按（步道、路段、日期）合并，并校验同组版本一致。"""
    grouped = defaultdict(lambda: {"defects": set(), "version": None})
    for row in rows:
        key = _key(row["route"], row["segment_id"], date.fromisoformat(row["inspection_date"]))
        group = grouped[key]
        group["defects"].add(row["defect"])
        if group["version"] is None:
            group["version"] = row["version"]
        elif group["version"] != row["version"]:
            raise ValueError(
                f"{key[0]} {key[1]} 在 {key[2].isoformat()} 的补传行填写了不同版本号，"
                "同一巡检日的整份更正请使用同一版本号。"
            )
    for (route, segment_id, day), group in grouped.items():
        if engine.CLEAN in group["defects"] and len(group["defects"]) > 1:
            raise ValueError(f"{route} {segment_id} 在 {day.isoformat()} 同时登记了无缺陷和具体缺陷。")
    return grouped


def _latest_active_ledger(db, route, segment_id):
    """该路段当前生效的最近巡检日台账记录。"""
    return db.scalar(
        select(InspectionLedger)
        .where(InspectionLedger.route == route, InspectionLedger.segment_id == segment_id,
               InspectionLedger.is_active.is_(True))
        .order_by(InspectionLedger.inspection_date.desc(), InspectionLedger.version.desc())
        .limit(1)
        .with_for_update()
    )


def _active_ledger_on(db, route, segment_id, day):
    return db.scalar(
        select(InspectionLedger)
        .where(InspectionLedger.route == route, InspectionLedger.segment_id == segment_id,
               InspectionLedger.inspection_date == day, InspectionLedger.is_active.is_(True))
        .with_for_update()
    )


def _refresh_state(db, route, segment_id, batch_id, changed):
    """按台账中仍有效的记录重算路段状态；状态真的变化才计入变动集合。"""
    latest = _latest_active_ledger(db, route, segment_id)
    state = db.scalar(
        select(SegmentState)
        .where(SegmentState.route == route, SegmentState.segment_id == segment_id)
        .with_for_update()
    )
    before = None if state is None else (state.inspection_date, tuple(state.defects))
    if latest is None:
        if state is not None:
            db.delete(state)
            changed.add(_state_key(route, segment_id))
        return
    after = (latest.inspection_date, tuple(sorted(set(latest.defects) - {engine.CLEAN})))
    if state is None:
        db.add(SegmentState(
            route=route, segment_id=segment_id, inspection_date=latest.inspection_date,
            defects=sorted(set(latest.defects) - {engine.CLEAN}), version=latest.version,
            ledger_id=latest.id, batch_id=batch_id,
        ))
        changed.add(_state_key(route, segment_id))
    elif before != after:
        state.inspection_date = latest.inspection_date
        state.defects = sorted(set(latest.defects) - {engine.CLEAN})
        state.version = latest.version
        state.ledger_id = latest.id
        state.batch_id = batch_id
        changed.add(_state_key(route, segment_id))


def _load_state_rows(db):
    states = db.scalars(select(SegmentState).order_by(SegmentState.route, SegmentState.segment_id))
    return [
        calculator.StateRow(
            route=item.route, segment_id=item.segment_id, inspection_date=item.inspection_date,
            defects=frozenset(item.defects),
        )
        for item in states
    ]


def _publish_checklist(db, result, params, batch_id, actor):
    previous = db.scalar(
        select(ChecklistVersion).where(ChecklistVersion.status == "active")
        .order_by(ChecklistVersion.version_no.desc()).limit(1).with_for_update()
    )
    next_no = 1 if previous is None else previous.version_no + 1
    version = ChecklistVersion(
        version_no=next_no, status="active", scope=params["route_scope"], params=params,
        total_segments=result.total, clean_segments=result.clean_count, rule_count=len(result.rules),
        changed_segments=[list(item) for item in result.changed_segments], summary=result.summary,
        rules_snapshot=[calculator.rule_snapshot(rule) for rule in result.rules],
        trigger_batch_id=batch_id, author=actor,
    )
    db.add(version)
    db.flush()
    for check in result.checks:
        db.add(ChecklistItem(
            checklist_version_id=version.id, route=check["route"], segment_id=check["segment_id"],
            inspection_date=date.fromisoformat(check["inspection_date"]),
            antecedent=check["antecedent"], consequent=check["consequent"], action=check["action"],
            evidence=calculator.check_evidence(check),
        ))
    if previous is not None:
        previous.status = "superseded"
    return version


def ingest(db, name, filename, csv_text, idempotency_key, actor, params=None):
    """归档一批补传并发布新版加查清单，返回 (批次, 是否本次新处理)。"""
    rows = parse_supplement(csv_text)  # 解析失败不落任何数据
    key = (idempotency_key or "").strip() or hashlib.sha256(csv_text.encode("utf-8")).hexdigest()
    params = calculator.normalize_params(params or {})

    acquire_lock(db)
    existing = db.scalar(select(SupplementBatch).where(SupplementBatch.idempotency_key == key))
    if existing is not None and existing.status == "complete":
        # 重跑已成功批次：幂等返回，不重复归档、不重复出单。
        return existing, False
    if existing is not None and existing.status == "processing":
        raise ValueError("同一补传批次正在处理中，请勿重复提交。")

    batch = existing or SupplementBatch(idempotency_key=key, author=actor)
    batch.name = name
    batch.filename = filename
    batch.status = "processing"
    batch.error = None
    batch.row_count = len(rows)
    batch.changed_segments = 0
    batch.replaced_versions = 0
    batch.skipped_versions = 0
    batch.checklist_version_id = None
    db.add(batch)
    db.flush()

    grouped = _group_rows(rows)
    changed = set()
    touched_segments = set()
    try:
        for (route, segment_id, day), group in grouped.items():
            touched_segments.add(_state_key(route, segment_id))
            active = _active_ledger_on(db, route, segment_id, day)
            if active is not None:
                if group["version"] < active.version:
                    # 晚到的低版本更正，已有更高版本生效，跳过。
                    batch.skipped_versions += 1
                    continue
                if group["version"] == active.version:
                    if set(active.defects) == group["defects"]:
                        # 同版本同内容，幂等跳过。
                        batch.skipped_versions += 1
                        continue
                    raise ValueError(
                        f"{route} {segment_id} 在 {day.isoformat()} 已有同版本（v{active.version}）"
                        "但内容不同，整份更正请提高版本号后再补传。"
                    )
                active.is_active = False
                active.superseded_batch_id = batch.id
                batch.replaced_versions += 1
            db.add(InspectionLedger(
                route=route, segment_id=segment_id, inspection_date=day, version=group["version"],
                defects=sorted(group["defects"]), batch_id=batch.id, is_active=True,
            ))
            db.flush()

        # 只重读有变动路段的原始记录：逐个受影响路段依据台账刷新状态投影。
        for route, segment_id in sorted(touched_segments):
            _refresh_state(db, route, segment_id, batch.id, changed)

        if not changed:
            # 全部为同版本重复内容：没有路段状态变化，不重算、不发新清单。
            current = db.scalar(
                select(ChecklistVersion).where(ChecklistVersion.status == "active")
                .order_by(ChecklistVersion.version_no.desc()).limit(1)
            )
            batch.status = "complete"
            batch.error = None
            batch.finished_at = now()
            batch.changed_segments = 0
            batch.checklist_version_id = current.id if current else None
            record(db, actor, "supplement.ingested", batch.id,
                   rows=batch.row_count, replaced=batch.replaced_versions,
                   skipped=batch.skipped_versions, changed_segments=0)
            return batch, True

        state_rows = _load_state_rows(db)
        result = calculator.recompute(state_rows, params, changed_segments=changed)
        version = _publish_checklist(db, result, params, batch.id, actor)

        batch.status = "complete"
        batch.error = None
        batch.finished_at = now()
        batch.changed_segments = len(changed)
        batch.checklist_version_id = version.id
        record(db, actor, "supplement.ingested", batch.id,
               rows=batch.row_count, replaced=batch.replaced_versions,
               skipped=batch.skipped_versions, changed_segments=batch.changed_segments)
        record(db, actor, "checklist.published", version.id,
               batch_id=batch.id, version_no=version.version_no, items=len(version.items))
        return batch, True
    except Exception:
        # 回滚本次全部归档与清单写入，旧版清单保持有效；仅留失败批次标记。
        db.rollback()
        raise


def mark_failed(db, name, filename, key, actor, reason):
    """主事务回滚后单独留痕，便于值班长看到失败原因并重跑（调用方负责提交）。"""
    existing = db.scalar(select(SupplementBatch).where(SupplementBatch.idempotency_key == key))
    batch = existing or SupplementBatch(idempotency_key=key, author=actor)
    batch.name = name
    batch.filename = filename
    batch.status = "failed"
    batch.error = reason[:500]
    batch.finished_at = now()
    db.add(batch)
    record(db, actor, "supplement.failed", batch.id, reason=reason[:500])
    db.flush()
    return batch
