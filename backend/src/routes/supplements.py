"""补传巡检台账与加查清单接口。"""
import hashlib

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from src.config.database import connection
from src.constants.enums import Role
from src.controllers.auth import allow, current
from src.models.identity import User
from src.models.supplement import (
    ChecklistVersion, InspectionLedger, SegmentState, SupplementBatch,
)
from src.schemas.requests import SupplementRequest
from src.services import supplements
from src.services.supplement_io import supplement_template
from src.utils import views

router = APIRouter(prefix='/supplement')


@router.get('/template')
def template(actor: User = Depends(current)):
    return Response(
        supplement_template(), media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition': "attachment; filename*=UTF-8''%E8%A1%A5%E4%BC%A0%E5%B7%A1%E6%A3%80%E6%A8%A1%E6%9D%BF.csv"},
    )


@router.post('/batches', status_code=201)
def upload(body: SupplementRequest, db: Session = Depends(connection),
           actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    key = (body.idempotency_key or '').strip() or hashlib.sha256(
        body.csv_text.encode('utf-8')).hexdigest()
    try:
        batch, created = supplements.ingest(
            db, body.name, body.filename, body.csv_text, key,
            actor.username, body.params,
        )
        db.commit()
        return {**views.supplement_batch(batch), 'created': created}
    except ValueError as error:
        # 主事务整体回滚：归档与清单写入全部撤销，旧版清单保持有效。
        db.rollback()
        # 单独留痕失败批次，便于值班长看到原因并用同幂等键重跑。
        failed = supplements.mark_failed(
            db, body.name, body.filename, key, actor.username, str(error)
        )
        db.commit()
        return {**views.supplement_batch(failed), 'created': True}


@router.get('/batches')
def batches(db: Session = Depends(connection), actor: User = Depends(current)):
    items = db.scalars(select(SupplementBatch).order_by(SupplementBatch.id.desc()).limit(100))
    return [views.supplement_batch(item) for item in items]


@router.get('/batches/{batch_id}')
def batch_detail(batch_id: int, db: Session = Depends(connection), actor: User = Depends(current)):
    item = db.get(SupplementBatch, batch_id)
    if not item:
        from fastapi import HTTPException
        raise HTTPException(404, '找不到该补传批次')
    return views.supplement_batch(item)


@router.get('/ledgers')
def ledgers(route: str | None = None, segment_id: str | None = None,
            offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
            db: Session = Depends(connection), actor: User = Depends(current)):
    query = select(InspectionLedger)
    if route:
        query = query.where(InspectionLedger.route == route)
    if segment_id:
        query = query.where(InspectionLedger.segment_id == segment_id)
    query = query.order_by(
        InspectionLedger.route, InspectionLedger.segment_id,
        InspectionLedger.inspection_date.desc(), InspectionLedger.version.desc(),
    )
    items = db.scalars(query.offset(offset).limit(limit))
    return [views.ledger_entry(item) for item in items]


@router.get('/segments')
def segments(db: Session = Depends(connection), actor: User = Depends(current)):
    items = db.scalars(select(SegmentState).order_by(SegmentState.route, SegmentState.segment_id))
    return [views.segment_state(item) for item in items]


@router.get('/checklists/latest')
def latest_checklist(db: Session = Depends(connection), actor: User = Depends(current)):
    item = db.scalar(
        select(ChecklistVersion).where(ChecklistVersion.status == 'active')
        .order_by(ChecklistVersion.version_no.desc()).limit(1)
    )
    return None if item is None else views.checklist_version(item, include_items=True)


@router.get('/checklists')
def checklists(db: Session = Depends(connection), actor: User = Depends(current)):
    items = db.scalars(select(ChecklistVersion).order_by(ChecklistVersion.version_no.desc()).limit(100))
    return [views.checklist_version(item) for item in items]


@router.get('/checklists/{version_id}')
def checklist_detail(version_id: int, db: Session = Depends(connection),
                     actor: User = Depends(current)):
    item = db.get(ChecklistVersion, version_id)
    if not item:
        from fastapi import HTTPException
        raise HTTPException(404, '找不到该清单版本')
    return views.checklist_version(item, include_items=True)
