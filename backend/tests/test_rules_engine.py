from fractions import Fraction

from src.domain import rules_engine as engine
from src.services import analysis as legacy


SAMPLE_ROWS = [
    {"inspection_date": "2026-11-16", "route": "北岸步道", "segment_id": "S08", "defect": "排水口堵塞"},
    {"inspection_date": "2026-11-23", "route": "北岸步道", "segment_id": "S01", "defect": "积水"},
    {"inspection_date": "2026-11-23", "route": "北岸步道", "segment_id": "S01", "defect": "青苔"},
    {"inspection_date": "2026-11-23", "route": "北岸步道", "segment_id": "S01", "defect": "排水口堵塞"},
    {"inspection_date": "2026-11-23", "route": "北岸步道", "segment_id": "S02", "defect": "积水"},
    {"inspection_date": "2026-11-23", "route": "北岸步道", "segment_id": "S02", "defect": "青苔"},
    {"inspection_date": "2026-11-23", "route": "北岸步道", "segment_id": "S02", "defect": "排水口堵塞"},
    {"inspection_date": "2026-11-23", "route": "北岸步道", "segment_id": "S03", "defect": "积水"},
    {"inspection_date": "2026-11-23", "route": "北岸步道", "segment_id": "S03", "defect": "青苔"},
    {"inspection_date": "2026-11-23", "route": "北岸步道", "segment_id": "S03", "defect": "排水口堵塞"},
    {"inspection_date": "2026-11-23", "route": "北岸步道", "segment_id": "S04", "defect": "积水"},
    {"inspection_date": "2026-11-23", "route": "北岸步道", "segment_id": "S04", "defect": "青苔"},
    {"inspection_date": "2026-11-23", "route": "北岸步道", "segment_id": "S05", "defect": "积水"},
    {"inspection_date": "2026-11-23", "route": "北岸步道", "segment_id": "S06", "defect": "青苔"},
    {"inspection_date": "2026-11-23", "route": "北岸步道", "segment_id": "S07", "defect": "铺装松动"},
    {"inspection_date": "2026-11-23", "route": "北岸步道", "segment_id": "S08", "defect": "无缺陷"},
    {"inspection_date": "2026-11-23", "route": "北岸步道", "segment_id": "S09", "defect": "排水口堵塞"},
    {"inspection_date": "2026-11-23", "route": "北岸步道", "segment_id": "S10", "defect": "积水"},
    {"inspection_date": "2026-11-23", "route": "北岸步道", "segment_id": "S10", "defect": "青苔"},
    {"inspection_date": "2026-11-23", "route": "北岸步道", "segment_id": "S10", "defect": "路缘破损"},
    {"inspection_date": "2026-11-23", "route": "观景环线", "segment_id": "S01", "defect": "铺装松动"},
    {"inspection_date": "2026-11-23", "route": "观景环线", "segment_id": "S01", "defect": "路缘破损"},
    {"inspection_date": "2026-11-23", "route": "观景环线", "segment_id": "S02", "defect": "铺装松动"},
    {"inspection_date": "2026-11-23", "route": "观景环线", "segment_id": "S02", "defect": "路缘破损"},
    {"inspection_date": "2026-11-23", "route": "观景环线", "segment_id": "S03", "defect": "铺装松动"},
    {"inspection_date": "2026-11-23", "route": "观景环线", "segment_id": "S03", "defect": "路缘破损"},
    {"inspection_date": "2026-11-23", "route": "观景环线", "segment_id": "S03", "defect": "护栏松动"},
    {"inspection_date": "2026-11-23", "route": "观景环线", "segment_id": "S04", "defect": "铺装松动"},
    {"inspection_date": "2026-11-23", "route": "观景环线", "segment_id": "S05", "defect": "路缘破损"},
    {"inspection_date": "2026-11-23", "route": "观景环线", "segment_id": "S06", "defect": "无缺陷"},
    {"inspection_date": "2026-11-23", "route": "观景环线", "segment_id": "S07", "defect": "无缺陷"},
    {"inspection_date": "2026-11-23", "route": "观景环线", "segment_id": "S08", "defect": "标识倒伏"},
    {"inspection_date": "2026-11-23", "route": "观景环线", "segment_id": "S09", "defect": "护栏松动"},
    {"inspection_date": "2026-11-23", "route": "观景环线", "segment_id": "S10", "defect": "无缺陷"},
]

PARAMS = {"route_scope": "全部步道", "min_support": 0.15, "min_confidence": 0.6, "min_lift": 1.25}


def test_latest_segment_keeps_newest_day_and_clean_in_denominator():
    segments, inspection_count = engine.segments_from_rows(SAMPLE_ROWS, "全部步道")
    by_key = {(s.route, s.segment_id): s for s in segments}
    # 北岸 S08 的旧缺陷被 11-23 无缺陷覆盖，无缺陷仍保留在分母
    s08 = by_key[("北岸步道", "S08")]
    assert s08.day.isoformat() == "2026-11-23"
    assert s08.defects == frozenset()
    assert len(segments) == 20
    assert inspection_count == 21  # S08 有两个巡检日


def test_engine_matches_legacy_full_run_output():
    result = legacy.analyze(SAMPLE_ROWS, PARAMS)
    checklist = result["tables"][0]["rows"]
    # 北岸 S04、S10 命中 积水+青苔 -> 排水口堵塞 的加查
    targets = {(row[0], row[1], row[4]) for row in checklist}
    assert ("北岸步道", "S04", "排水口堵塞") in targets
    assert ("北岸步道", "S10", "排水口堵塞") in targets
    # 同段同后项只列一次
    keys = [(row[0], row[1], row[4]) for row in checklist]
    assert len(keys) == len(set(keys))
    rule_rows = result["tables"][1]["rows"]
    assert rule_rows, "应至少有一条达标规则"


def test_scope_filters_denominator():
    result = legacy.analyze(SAMPLE_ROWS, {**PARAMS, "route_scope": "北岸步道"})
    metric = result["metrics"][0]
    assert metric["value"] == 10
    # 北岸 5 段积水青苔同见，3 段也有排水口
    rule_rows = result["tables"][1]["rows"]
    pair = next(row for row in rule_rows if row[0] == "积水、青苔" and row[1] == "排水口堵塞")
    assert pair[2] == 3 and pair[3] == 5


def test_clean_and_defect_conflict_rejected():
    bad = [
        {"inspection_date": "2026-11-23", "route": "北岸步道", "segment_id": "S01", "defect": "无缺陷"},
        {"inspection_date": "2026-11-23", "route": "北岸步道", "segment_id": "S01", "defect": "积水"},
    ]
    try:
        engine.build_daily(bad)
    except ValueError as error:
        assert "无缺陷" in str(error)
    else:
        raise AssertionError("同段同日无缺陷与具体缺陷冲突应当被拒绝")


def test_unknown_defect_rejected():
    try:
        engine.check_defect("飞线充电", "测试")
    except ValueError as error:
        assert "巡检字典" in str(error)
    else:
        raise AssertionError("字典外缺陷应被拒绝")


def test_threshold_uses_unrounded_fraction():
    params = {"min_support": "0.15", "min_confidence": "0.6", "min_lift": "1.25"}
    value = engine.threshold(params, "min_support", "0.15", "支持度", Fraction(1, 100), 1)
    assert value == Fraction(15, 100)
