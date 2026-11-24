"""补传接口端到端测试：TestClient + SQLite，验证鉴权、幂等与版本切换。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.config.database as database
from src.config.database import Base
from src.main import app
from src.models.identity import User
from src.services import supplements
from src.utils.security import hash_password


@pytest.fixture()
def client():
    engine = create_engine(
        'sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool,
    )
    factory = sessionmaker(engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    supplements.ensure_lock_row(engine)
    database.engine = engine
    database.Session = factory
    with factory() as db:
        db.add(User(username='analyst', role='analyst', password_hash=hash_password('pw-2611')))
        db.add(User(username='viewer', role='viewer', password_hash=hash_password('pw-2611')))
        db.commit()
    with TestClient(app) as test_client:
        yield test_client, factory


def token(test_client, username):
    response = test_client.post('/auth/login', json={'username': username, 'password': 'pw-2611'})
    assert response.status_code == 200
    return response.json()['token']


def headers(test_client, username):
    return {'Authorization': f'Bearer {token(test_client, username)}'}


CSV_TEXT = (
    "inspection_date,route,segment_id,defect,version\n"
    "2026-11-23,北岸步道,S01,积水,1\n"
    "2026-11-23,北岸步道,S01,青苔,1\n"
    "2026-11-23,北岸步道,S01,排水口堵塞,1\n"
    "2026-11-23,北岸步道,S02,积水,1\n"
    "2026-11-23,北岸步道,S02,青苔,1\n"
    "2026-11-23,北岸步道,S03,积水,1\n"
    "2026-11-23,北岸步道,S04,护栏松动,1\n"
)


def test_supplement_flow_and_idempotent_replay(client):
    test_client, factory = client
    auth = headers(test_client, 'analyst')

    # 未登录被拒绝
    assert test_client.post('/supplement/batches', json={}).status_code == 401

    payload = {'name': '晚到补传', 'filename': 'late.csv', 'csv_text': CSV_TEXT,
               'idempotency_key': 'key-1', 'params': {'route_scope': '北岸步道'}}
    first = test_client.post('/supplement/batches', json=payload, headers=auth)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body['status'] == 'complete' and body['created'] is True
    assert body['changed_segments'] == 4

    # 同幂等键重跑：不重复出单
    replay = test_client.post('/supplement/batches', json=payload, headers=auth)
    assert replay.status_code == 201
    assert replay.json()['created'] is False
    assert replay.json()['id'] == body['id']

    # 只读角色不能补传
    denied = test_client.post('/supplement/batches', json=payload,
                              headers=headers(test_client, 'viewer'))
    assert denied.status_code == 403

    # 最新清单带命中依据
    latest = test_client.get('/supplement/checklists/latest', headers=auth).json()
    assert latest['version_no'] == 1 and latest['status'] == 'active'
    assert latest['item_count'] == len(latest['items'])
    for item in latest['items']:
        assert item['evidence']['antecedent'] and item['action']

    # 路段状态与台账可查询
    states = test_client.get('/supplement/segments', headers=auth).json()
    assert len(states) == 4
    ledgers = test_client.get('/supplement/ledgers?route=北岸步道', headers=auth).json()
    assert ledgers and all(row['route'] == '北岸步道' for row in ledgers)

    # 第二批：同日 v2 整份更正 S01（三行统一版本号），清单升到 v2，旧版保留可追溯
    correction = (
        "inspection_date,route,segment_id,defect,version\n"
        "2026-11-23,北岸步道,S01,积水,2\n"
        "2026-11-23,北岸步道,S01,青苔,2\n"
        "2026-11-23,北岸步道,S02,积水,1\n"
        "2026-11-23,北岸步道,S02,青苔,1\n"
        "2026-11-23,北岸步道,S03,积水,1\n"
        "2026-11-23,北岸步道,S04,护栏松动,1\n"
    )
    second = test_client.post('/supplement/batches', json={
        'name': '同日更正', 'filename': 'fix.csv', 'csv_text': correction,
        'idempotency_key': 'key-2', 'params': {'route_scope': '北岸步道'},
    }, headers=auth)
    assert second.status_code == 201, second.text
    assert second.json()['replaced_versions'] == 1
    versions = test_client.get('/supplement/checklists', headers=auth).json()
    assert [v['version_no'] for v in versions] == [2, 1]
    assert versions[0]['status'] == 'active' and versions[1]['status'] == 'superseded'
    detail = test_client.get(f"/supplement/checklists/{versions[1]['id']}", headers=auth).json()
    assert detail['version_no'] == 1  # 旧版整份保留，失败/切换都不丢依据


def test_invalid_supplement_returns_failed_batch_and_keeps_old_checklist(client):
    test_client, factory = client
    auth = headers(test_client, 'analyst')
    payload = {'name': '首批', 'filename': 'a.csv', 'csv_text': CSV_TEXT,
               'idempotency_key': 'k-ok', 'params': {'route_scope': '北岸步道'}}
    assert test_client.post('/supplement/batches', json=payload, headers=auth).status_code == 201

    bad = ('inspection_date,route,segment_id,defect,version\n'
           '2026-11-24,北岸步道,S08,无缺陷,1\n'
           '2026-11-24,北岸步道,S08,积水,1\n')
    response = test_client.post('/supplement/batches', json={
        'name': '坏批次', 'filename': 'b.csv', 'csv_text': bad,
        'idempotency_key': 'k-bad', 'params': {'route_scope': '北岸步道'},
    }, headers=auth)
    # 业务校验失败不落主事务，但留失败批次痕迹
    assert response.status_code == 201
    failed = response.json()
    assert failed['status'] == 'failed' and failed['error']
    # 失败批次已持久化，可在批次列表追溯
    listed = test_client.get('/supplement/batches', headers=auth).json()
    persisted = next(row for row in listed if row['idempotency_key'] == 'k-bad')
    assert persisted['status'] == 'failed' and persisted['error']
    latest = test_client.get('/supplement/checklists/latest', headers=auth).json()
    assert latest['version_no'] == 1  # 旧版清单完整保留


def test_unknown_route_404(client):
    test_client, _ = client
    auth = headers(test_client, 'analyst')
    assert test_client.get('/supplement/checklists/9999', headers=auth).status_code == 404
    assert test_client.get('/supplement/batches/9999', headers=auth).status_code == 404


def test_failed_batch_can_be_fixed_and_rerun_with_same_key(client):
    test_client, _ = client
    auth = headers(test_client, 'analyst')
    base = {'name': '可重试批次', 'filename': 'r.csv', 'idempotency_key': 'retry-key',
            'params': {'route_scope': '北岸步道'}}
    bad = ('inspection_date,route,segment_id,defect,version\n'
           '2026-11-24,北岸步道,S08,无缺陷,1\n'
           '2026-11-24,北岸步道,S08,积水,1\n')
    first = test_client.post('/supplement/batches', json={**base, 'csv_text': bad}, headers=auth)
    assert first.json()['status'] == 'failed'

    # 修正后用同一幂等键重跑：复用失败批次记录，转为成功并出第一版清单
    good = ('inspection_date,route,segment_id,defect,version\n'
            '2026-11-24,北岸步道,S08,无缺陷,1\n')
    second = test_client.post('/supplement/batches', json={**base, 'csv_text': good}, headers=auth)
    body = second.json()
    assert body['status'] == 'complete' and body['created'] is True
    assert body['id'] == first.json()['id']  # 同键复用，不新增重复批次
    latest = test_client.get('/supplement/checklists/latest', headers=auth).json()
    assert latest['version_no'] == 1
    states = test_client.get('/supplement/segments', headers=auth).json()
    s08 = next(row for row in states if row['segment_id'] == 'S08')
    assert s08['defects'] == []  # 无缺陷计入分母
    # 再次重跑成功批次：幂等不重复出单
    third = test_client.post('/supplement/batches', json={**base, 'csv_text': good}, headers=auth)
    assert third.json()['created'] is False
    assert test_client.get('/supplement/checklists', headers=auth).json().__len__() == 1
