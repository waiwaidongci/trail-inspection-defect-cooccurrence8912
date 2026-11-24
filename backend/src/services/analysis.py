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
    defects: set[str]


class Rule(NamedTuple):
    antecedent: tuple[str, ...]
    consequent: str
    joint_count: int
    antecedent_count: int
    consequent_count: int
    support: Fraction
    confidence: Fraction
    lift: Fraction


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}不能为空。")
    return value.strip()


def _threshold(params, key, default, label, minimum, maximum=None):
    try:
        value = Fraction(str(params.get(key, default)))
    except (ValueError, ZeroDivisionError):
        raise ValueError(f"{label}必须是有限数值。") from None
    if value < minimum or (maximum is not None and value > maximum):
        if maximum is None:
            raise ValueError(f"{label}至少为 {float(minimum):g}。")
        raise ValueError(f"{label}必须介于 {float(minimum):g} 和 {maximum} 之间。")
    return value


def _segments(rows, scope):
    inspections = defaultdict(set)
    for number, row in enumerate(rows, 1):
        prefix = f"第 {number} 条巡检记录的"
        route = _text(row.get("route"), f"{prefix}步道名称")
        segment_id = _text(row.get("segment_id"), f"{prefix}路段编号")
        raw_day = _text(row.get("inspection_date"), f"{prefix}完整巡检日期")
        defect = _text(row.get("defect"), f"{prefix}缺陷类型")
        if route == ALL_ROUTES:
            raise ValueError(f"{prefix}步道名称应填写具体步道。")
        try:
            day = date.fromisoformat(raw_day)
            if day.isoformat() != raw_day:
                raise ValueError
        except ValueError:
            raise ValueError(f"{prefix}完整巡检日期必须为有效的 YYYY-MM-DD 日期。") from None
        if defect != CLEAN and defect not in CHECK_ACTIONS:
            raise ValueError(f"{prefix}缺陷类型“{defect}”不在巡检字典中。")
        inspections[route, segment_id, day].add(defect)

    latest = {}
    for (route, segment_id, day), defects in sorted(inspections.items()):
        if CLEAN in defects and len(defects) > 1:
            raise ValueError(f"{route} {segment_id} 在 {day.isoformat()} 同时登记了无缺陷和具体缺陷。")
        if scope == ALL_ROUTES or route == scope:
            latest[route, segment_id] = Segment(route, segment_id, day, defects - {CLEAN})
    if not latest:
        raise ValueError("没有可用巡检路段，请核对步道范围或导入完整巡检记录。")
    return list(latest.values()), len(inspections)


def _apriori(segments, min_support):
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


def _rules(frequent, total, min_confidence, min_lift):
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


def _scores(rule):
    return [
        round(float(rule.support * 100), 2),
        round(float(rule.confidence * 100), 2),
        round(float(rule.lift), 3),
    ]


def _checklist(segments, rules):
    checks = []
    for segment in segments:
        selected = {}
        for rule in rules:
            if rule.consequent not in segment.defects and segment.defects.issuperset(rule.antecedent):
                selected.setdefault(rule.consequent, rule)
        for consequent, rule in selected.items():
            checks.append([
                segment.route, segment.segment_id, segment.day.isoformat(), "、".join(rule.antecedent),
                consequent, CHECK_ACTIONS[consequent], *_scores(rule),
            ])
    return checks


def analyze(rows, params):
    scope = _text(params.get("route_scope", ALL_ROUTES), "步道范围")
    min_support = _threshold(params, "min_support", "0.15", "最低支持度", Fraction(1, 100), 1)
    min_confidence = _threshold(params, "min_confidence", "0.60", "最低置信度", Fraction(1, 100), 1)
    min_lift = _threshold(params, "min_lift", "1.25", "最低提升度", Fraction(101, 100))
    segments, inspection_count = _segments(rows, scope)
    total = len(segments)
    frequent, singletons = _apriori(segments, min_support)
    rules = _rules(frequent, total, min_confidence, min_lift)
    checks = _checklist(segments, rules)
    affected = len({(row[0], row[1]) for row in checks})
    clean_count = sum(not segment.defects for segment in segments)
    summary = f"{scope}纳入 {total} 个路段（{clean_count} 个无缺陷），共 {len(rules)} 条达标规则。"
    if checks:
        route, segment_id, _, _, defect, *_evidence = checks[0]
        summary += (
            f"下一班对 {affected} 个路段加查 {len(checks)} 项，"
            f"其中 {route} {segment_id} 需加查{defect}。"
        )
    elif rules:
        summary += "已登记的缺陷覆盖了现有规则命中的后项，按原清单巡检。"
    else:
        summary += "当前范围内没有组合同时满足门槛，按原清单巡检。"

    return {
        "summary": summary,
        "metrics": [
            {"label": "纳入路段", "value": total, "detail": f"{scope}，每段取最近一次；导入台账含 {inspection_count} 份巡检。"},
            {"label": "无缺陷路段", "value": clean_count, "detail": "计入支持度分母。"},
            {"label": "达标规则", "value": len(rules), "detail": "支持度、置信度、提升度与至少两段同见同时达标。"},
            {"label": "新增加查项", "value": len(checks), "detail": f"涉及 {affected} 个路段，同段同一加查项只列一次。"},
        ],
        "tables": [
            {
                "title": "下一班逐段加查清单",
                "columns": [
                    "步道", "路段编号", "最近巡检", "已登记前项", "加查缺陷", "现场核验动作",
                    "支持度（%）", "置信度（%）", "提升度（倍）",
                ],
                "rows": checks,
            },
            {
                "title": "达标共现规则",
                "columns": [
                    "已登记前项", "关联后项", "同见路段", "前项路段", "后项路段",
                    "支持度（%）", "置信度（%）", "提升度（倍）",
                ],
                "rows": [
                    ["、".join(rule.antecedent), rule.consequent, rule.joint_count,
                     rule.antecedent_count, rule.consequent_count, *_scores(rule)]
                    for rule in rules
                ],
            },
            {
                "title": "纳入分析的最近巡检",
                "columns": ["步道", "路段编号", "巡检日期", "登记结果"],
                "rows": [
                    [segment.route, segment.segment_id, segment.day.isoformat(),
                     "、".join(sorted(segment.defects)) or CLEAN]
                    for segment in segments
                ],
            },
        ],
        "notes": [
            "同一巡检日的缺陷去重，各路段以最近一次巡检为准。步道范围同时约束规则分母和加查清单。",
            "支持度＝同见段数 ÷ 全部纳入段数；置信度＝同见段数 ÷ 前项段数；提升度＝置信度 ÷ 后项基准频率。门槛按未舍入值判断。",
            "同段同一后项被多条规则命中时，依次按置信度、提升度、同见段数选取依据。加查需由巡检员现场确认，派单以核验记录为准。",
        ],
        "series": [
            {"label": defect, "value": count}
            for defect, count in sorted(singletons.items(), key=lambda item: (-item[1], item[0]))
        ],
    }
