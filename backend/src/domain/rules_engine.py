"""共现规则纯算法层。

只依赖标准库，供旧的整批运行（services.analysis.analyze）与新的补传台账
增量试算（domain.calculator）共用，保证两条链路的口径完全一致：

- 同巡检日缺陷去重，无缺陷与具体缺陷互斥；
- 各路段只保留最近一次巡检，明确无缺陷的路段仍留在分母；
- Apriori 逐层筛选二/三项组合，再拆成指向单个缺陷的规则；
- 支持度、置信度、提升度与至少两段同见同时达标才出规则；
- 最新一轮未登记后项的路段才生成加查，同段同后项只保留一条命中依据。
"""
from collections import Counter, defaultdict
from datetime import date
from fractions import Fraction
from itertools import combinations
from typing import NamedTuple

ALL_ROUTES = "全部步道"
CLEAN = "无缺陷"
MIN_JOINT_COUNT = 2
CHECK_ACTIONS = {
    "积水": "查看低点和横坡边缘有无存水或水痕，记录位置和范围。",
    "青苔": "查看潮湿边缘和背阴侧有无苔斑，拍照记录覆盖范围。",
    "排水口堵塞": "查看该段低点的格栅及出水端，记录落叶、淤泥及通水情况。",
    "铺装松动": "查看破损点周边铺装有无翘边、缝隙和松动，标记砖块位置。",
    "路缘破损": "沿相邻路缘检查缺口、错台及脱落件，记录长度和位置。",
    "护栏松动": "查看立柱底座及可见连接件，记录偏斜或松脱位置。",
    "标识倒伏": "核对该段导向牌及底座，记录倾倒或脱位的标识编号。",
}


class Segment(NamedTuple):
    route: str
    segment_id: str
    day: date
    defects: frozenset


class Rule(NamedTuple):
    antecedent: tuple[str, ...]
    consequent: str
    joint_count: int
    antecedent_count: int
    consequent_count: int
    support: Fraction
    confidence: Fraction
    lift: Fraction


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}不能为空。")
    return value.strip()


def threshold(params, key, default, label, minimum, maximum=None):
    try:
        value = Fraction(str(params.get(key, default)))
    except (ValueError, ZeroDivisionError):
        raise ValueError(f"{label}必须是有限数值。") from None
    if value < minimum or (maximum is not None and value > maximum):
        if maximum is None:
            raise ValueError(f"{label}至少为 {float(minimum):g}。")
        raise ValueError(f"{label}必须介于 {float(minimum):g} 和 {maximum} 之间。")
    return value


def parse_day(raw_day, label):
    try:
        day = date.fromisoformat(raw_day)
        if day.isoformat() != raw_day:
            raise ValueError
    except ValueError:
        raise ValueError(f"{label}必须为有效的 YYYY-MM-DD 日期。") from None
    return day


def check_defect(defect, label):
    if defect != CLEAN and defect not in CHECK_ACTIONS:
        raise ValueError(f"{label}缺陷类型“{defect}”不在巡检字典中。")
    return defect


def build_daily(rows):
    """把原始巡检行合并为 (步道, 路段, 日期) -> 当日缺陷集合，并做一致性校验。"""
    inspections = defaultdict(set)
    for number, row in enumerate(rows, 1):
        prefix = f"第 {number} 条巡检记录的"
        route = text(row.get("route"), f"{prefix}步道名称")
        segment_id = text(row.get("segment_id"), f"{prefix}路段编号")
        raw_day = text(row.get("inspection_date"), f"{prefix}完整巡检日期")
        defect = text(row.get("defect"), f"{prefix}缺陷类型")
        if route == ALL_ROUTES:
            raise ValueError(f"{prefix}步道名称应填写具体步道。")
        day = parse_day(raw_day, f"{prefix}完整巡检日期")
        check_defect(defect, f"{prefix}")
        inspections[route, segment_id, day].add(defect)
    for (route, segment_id, day), defects in inspections.items():
        if CLEAN in defects and len(defects) > 1:
            raise ValueError(f"{route} {segment_id} 在 {day.isoformat()} 同时登记了无缺陷和具体缺陷。")
    return inspections


def latest_segments(inspections, scope=ALL_ROUTES):
    """每个 (步道, 路段) 只保留最近日期的一份完整记录。"""
    latest = {}
    for (route, segment_id, day), defects in sorted(inspections.items()):
        if scope != ALL_ROUTES and route != scope:
            continue
        current = latest.get((route, segment_id))
        if current is None or day >= current.day:
            latest[route, segment_id] = Segment(
                route, segment_id, day, frozenset(defects - {CLEAN})
            )
    if not latest:
        raise ValueError("没有可用巡检路段，请核对步道范围或导入完整巡检记录。")
    return list(latest.values())


def segments_from_rows(rows, scope=ALL_ROUTES):
    inspections = build_daily(rows)
    return latest_segments(inspections, scope), len(inspections)


def apriori(segments, min_support):
    min_count = max(MIN_JOINT_COUNT, min_support * len(segments))
    singletons = Counter(defect for segment in segments for defect in segment.defects)
    level = {(defect,): count for defect, count in singletons.items() if count >= min_count}
    frequent = dict(level)
    for size in (2, 3):
        candidates = set()
        for left, right in combinations(sorted(level), 2):
            if left[:-1] == right[:-1]:
                candidate = (*left, right[-1])
                if all(subset in level for subset in combinations(candidate, size - 1)):
                    candidates.add(candidate)
        if not candidates:
            break
        counts = Counter(
            itemset
            for segment in segments
            for itemset in combinations(sorted(segment.defects), size)
            if itemset in candidates
        )
        level = {items: count for items, count in counts.items() if count >= min_count}
        frequent.update(level)
        if not level:
            break
    return frequent, singletons


def build_rules(frequent, total, min_confidence, min_lift):
    rules = []
    for itemset, joint_count in frequent.items():
        if len(itemset) == 1:
            continue
        for consequent in itemset:
            antecedent = tuple(defect for defect in itemset if defect != consequent)
            antecedent_count = frequent[antecedent]
            consequent_count = frequent[(consequent,)]
            confidence = Fraction(joint_count, antecedent_count)
            lift = confidence / Fraction(consequent_count, total)
            if confidence >= min_confidence and lift >= min_lift:
                rules.append(Rule(
                    antecedent, consequent, joint_count, antecedent_count, consequent_count,
                    Fraction(joint_count, total), confidence, lift,
                ))
    return sorted(rules, key=lambda rule: (
        -rule.confidence, -rule.lift, -rule.joint_count, rule.antecedent, rule.consequent,
    ))


def scores(rule):
    return [
        round(float(rule.support * 100), 2),
        round(float(rule.confidence * 100), 2),
        round(float(rule.lift), 3),
    ]


def build_checklist(segments, rules):
    """返回逐段加查项，命中依据（规则）随清单一起产出。"""
    checks = []
    for segment in sorted(segments, key=lambda item: (item.route, item.segment_id)):
        selected = {}
        for rule in rules:
            if rule.consequent not in segment.defects and segment.defects.issuperset(rule.antecedent):
                selected.setdefault(rule.consequent, rule)
        for consequent, rule in selected.items():
            checks.append({
                "route": segment.route,
                "segment_id": segment.segment_id,
                "inspection_date": segment.day.isoformat(),
                "antecedent": "、".join(rule.antecedent),
                "consequent": consequent,
                "action": CHECK_ACTIONS[consequent],
                "rule": rule,
            })
    return checks
