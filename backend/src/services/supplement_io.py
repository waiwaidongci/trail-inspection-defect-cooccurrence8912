"""补传巡检 CSV 解析。

兼容底册模板的 4 列（inspection_date, route, segment_id, defect），
并支持第 5 列 version（同日更正版本号，留空按 1 处理）。晚到记录按巡检
日期归档，解析阶段只做字段与字典校验，归档与版本裁决在 service 层完成。
"""
import csv
import io

from src.domain import rules_engine as engine

BASE_HEADERS = ["inspection_date", "route", "segment_id", "defect"]
VERSION_HEADER = "version"
MAX_ROWS = 5000


def supplement_template():
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow([*BASE_HEADERS, VERSION_HEADER])
    writer.writerow(["2026-11-23", "北岸步道", "S04", "积水", 1])
    writer.writerow(["2026-11-23", "北岸步道", "S04", "青苔", 1])
    writer.writerow(["2026-11-23", "北岸步道", "S04", "青苔", 2])
    return "\ufeff" + buffer.getvalue()


def parse_supplement(text):
    reader = csv.DictReader(io.StringIO(text.removeprefix("\ufeff"), newline=""), strict=True)
    headers = reader.fieldnames or []
    if not headers:
        raise ValueError("CSV 表头与字段说明不符，请下载模板检查列名。")
    accepted = {*BASE_HEADERS, VERSION_HEADER}
    if not set(BASE_HEADERS) <= set(headers) or not set(headers) <= accepted or len(headers) != len(set(headers)):
        raise ValueError("CSV 表头应为 inspection_date, route, segment_id, defect，可附 version 版本号列。")
    has_version = VERSION_HEADER in headers
    rows = []
    try:
        for raw in reader:
            if len(rows) >= MAX_ROWS:
                raise ValueError("一个补传批次最多导入 5000 行。")
            line = reader.line_num
            if None in raw or any(value is None for value in raw.values()):
                raise ValueError("工作表的列数与表头不一致。")
            prefix = f"CSV 第 {line} 行的"
            route = engine.text(raw.get("route"), f"{prefix}步道名称")
            if route == engine.ALL_ROUTES:
                raise ValueError(f"{prefix}步道名称应填写具体步道。")
            segment_id = engine.text(raw.get("segment_id"), f"{prefix}路段编号")
            raw_day = engine.text(raw.get("inspection_date"), f"{prefix}完整巡检日期")
            day = engine.parse_day(raw_day, f"{prefix}完整巡检日期")
            defect = engine.check_defect(engine.text(raw.get("defect"), f"{prefix}缺陷类型"), f"{prefix}")
            version = 1
            if has_version:
                raw_version = (raw.get(VERSION_HEADER) or "").strip()
                if raw_version:
                    try:
                        version = int(raw_version)
                    except ValueError:
                        raise ValueError(f"{prefix}版本号必须为不小于 1 的整数。") from None
                    if version < 1:
                        raise ValueError(f"{prefix}版本号必须为不小于 1 的整数。")
            rows.append({
                "inspection_date": day.isoformat(),
                "route": route,
                "segment_id": segment_id,
                "defect": defect,
                "version": version,
            })
    except csv.Error:
        raise ValueError(f"CSV 第 {reader.line_num} 行附近格式不正确。") from None
    if not rows:
        raise ValueError("补传 CSV 至少需要一行有效数据。")
    return rows
