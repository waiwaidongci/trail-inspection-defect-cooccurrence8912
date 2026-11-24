"""补传台账的增量试算领域层。

输入是各路段当前生效状态（SegmentState 的内存投影），输出清单版本所需的
规则、加查项与统计。规则引擎与整批运行共用 src.domain.rules_engine，因此
无缺陷路段进出分母后，其他路段的规则达标与否、加查的增撤都随之自动变化。
"""
from dataclasses import dataclass, field
from datetime import date
from fractions import Fraction

from src.domain import rules_engine as engine
from src.domain.rules_engine import ALL_ROUTES


@dataclass(frozen=True)
class StateRow:
    route: str
    segment_id: str
    inspection_date: date
    defects: frozenset


@dataclass
class ChecklistResult:
    segments: list
    rules: list
    checks: list
    total: int
    clean_count: int
    affected: int
    summary: str
    changed_segments: list = field(default_factory=list)


def normalize_params(params):
    """补传单参数沿用试算口径；范围默认全步道。"""
    scope = engine.text(params.get("route_scope", ALL_ROUTES), "步道范围")
    min_support = engine.threshold(params, "min_support", "0.15", "最低支持度", Fraction(1, 100), 1)
    min_confidence = engine.threshold(params, "min_confidence", "0.60", "最低置信度", Fraction(1, 100), 1)
    min_lift = engine.threshold(params, "min_lift", "1.25", "最低提升度", Fraction(101, 100))
    return {
        "route_scope": scope,
        "min_support": str(min_support),
        "min_confidence": str(min_confidence),
        "min_lift": str(min_lift),
    }


def to_segments(state_rows, scope):
    """只把范围内的路段状态转成引擎输入；无缺陷路段同样进入分母。"""
    segments = []
    for row in state_rows:
        if scope != ALL_ROUTES and row.route != scope:
            continue
        segments.append(engine.Segment(row.route, row.segment_id, row.inspection_date, row.defects))
    if not segments:
        raise ValueError("台账中没有该步道范围的巡检路段，无法试算。")
    return segments


def recompute(state_rows, params, changed_segments=None):
    """从路段当前状态整体重算规则与清单。

    增量体现在调用方只重读有变动路段的原始记录来更新状态投影；规则与清单
    必须全局重算，因为分母和达标门槛会随任意路段进出而变化。
    """
    normalized = normalize_params(params)
    scope = normalized["route_scope"]
    min_support = Fraction(normalized["min_support"])
    min_confidence = Fraction(normalized["min_confidence"])
    min_lift = Fraction(normalized["min_lift"])

    segments = to_segments(state_rows, scope)
    total = len(segments)
    frequent, _singletons = engine.apriori(segments, min_support)
    rules = engine.build_rules(frequent, total, min_confidence, min_lift)
    checks = engine.build_checklist(segments, rules)
    affected = len({(item["route"], item["segment_id"]) for item in checks})
    clean_count = sum(not segment.defects for segment in segments)

    summary = (
        f"{scope}台账纳入 {total} 个路段（{clean_count} 个无缺陷），"
        f"共 {len(rules)} 条达标规则，下一班加查 {len(checks)} 项、涉及 {affected} 个路段。"
    )
    return ChecklistResult(
        segments=sorted(segments, key=lambda item: (item.route, item.segment_id)),
        rules=rules,
        checks=checks,
        total=total,
        clean_count=clean_count,
        affected=affected,
        summary=summary,
        changed_segments=sorted(changed_segments or []),
    )


def rule_snapshot(rule):
    """规则的可 JSON 化快照，作为清单命中依据随版本一起保存。"""
    return {
        "antecedent": list(rule.antecedent),
        "consequent": rule.consequent,
        "joint_count": rule.joint_count,
        "antecedent_count": rule.antecedent_count,
        "consequent_count": rule.consequent_count,
        "support": str(rule.support),
        "confidence": str(rule.confidence),
        "lift": str(rule.lift),
        "scores": engine.scores(rule),
    }


def check_evidence(check):
    rule = check["rule"]
    return {
        "antecedent": list(rule.antecedent),
        "consequent": rule.consequent,
        "joint_count": rule.joint_count,
        "antecedent_count": rule.antecedent_count,
        "consequent_count": rule.consequent_count,
        "support_pct": engine.scores(rule)[0],
        "confidence_pct": engine.scores(rule)[1],
        "lift": engine.scores(rule)[2],
    }
