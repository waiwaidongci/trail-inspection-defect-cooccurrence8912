"""整批运行的试算入口：复用共现规则引擎并组装成运行结果表结构。"""
from fractions import Fraction

from src.domain import rules_engine as engine

ALL_ROUTES = engine.ALL_ROUTES
CLEAN = engine.CLEAN
MIN_JOINT_COUNT = engine.MIN_JOINT_COUNT
CHECK_ACTIONS = engine.CHECK_ACTIONS


def validate_rows(rows):
    # 复用同一套字段与口径校验，导入期即可发现问题。
    engine.build_daily(rows)


def analyze(rows, params):
    scope = engine.text(params.get("route_scope", ALL_ROUTES), "步道范围")
    min_support = engine.threshold(params, "min_support", "0.15", "最低支持度", Fraction(1, 100), 1)
    min_confidence = engine.threshold(params, "min_confidence", "0.60", "最低置信度", Fraction(1, 100), 1)
    min_lift = engine.threshold(params, "min_lift", "1.25", "最低提升度", Fraction(101, 100))
    segments, inspection_count = engine.segments_from_rows(rows, scope)
    total = len(segments)
    frequent, singletons = engine.apriori(segments, min_support)
    rules = engine.build_rules(frequent, total, min_confidence, min_lift)
    checks = engine.build_checklist(segments, rules)
    affected = len({(item["route"], item["segment_id"]) for item in checks})
    clean_count = sum(not segment.defects for segment in segments)
    summary = f"{scope}纳入 {total} 个路段（{clean_count} 个无缺陷），共 {len(rules)} 条达标规则。"
    if checks:
        first = checks[0]
        summary += (
            f"下一班对 {affected} 个路段加查 {len(checks)} 项，"
            f"其中 {first['route']} {first['segment_id']} 需加查{first['consequent']}。"
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
                "rows": [
                    [item["route"], item["segment_id"], item["inspection_date"], item["antecedent"],
                     item["consequent"], item["action"], *engine.scores(item["rule"])]
                    for item in checks
                ],
            },
            {
                "title": "达标共现规则",
                "columns": [
                    "已登记前项", "关联后项", "同见路段", "前项路段", "后项路段",
                    "支持度（%）", "置信度（%）", "提升度（倍）",
                ],
                "rows": [
                    ["、".join(rule.antecedent), rule.consequent, rule.joint_count,
                     rule.antecedent_count, rule.consequent_count, *engine.scores(rule)]
                    for rule in rules
                ],
            },
            {
                "title": "纳入分析的最近巡检",
                "columns": ["步道", "路段编号", "巡检日期", "登记结果"],
                "rows": [
                    [segment.route, segment.segment_id, segment.day.isoformat(),
                     "、".join(sorted(segment.defects)) or CLEAN]
                    for segment in sorted(segments, key=lambda item: (item.route, item.segment_id))
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
