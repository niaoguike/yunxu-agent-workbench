"""Bounded CSV snapshots and validated, immutable chart versions."""
import csv
import io
import math
import json
from typing import Literal
from pydantic import BaseModel, Field
from .store import now, uid


def parse_csv(raw):
    if len(raw) > 2_000_000:
        raise ValueError("CSV 最大 2 MB")
    try:
        reader = csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
        columns = next(reader)
        if not 2 <= len(columns) <= 40 or any(not c.strip() for c in columns):
            raise ValueError("CSV 需要 2–40 个非空列名")
        if len(set(columns)) != len(columns) or any(len(c) > 120 for c in columns):
            raise ValueError("列名不能重复，且最多 120 字符")
        rows = []
        for row in reader:
            if not row:
                continue
            if len(row) != len(columns):
                raise ValueError("CSV 每行列数必须与表头一致")
            rows.append(row)
            if len(rows) > 1000:
                raise ValueError("CSV 最多 1000 行数据")
        if not rows:
            raise ValueError("CSV 没有数据")
    except (UnicodeError, csv.Error, StopIteration) as error:
        raise ValueError("请上传有效的 UTF-8 CSV，含表头和数据") from error
    numeric = [c for i, c in enumerate(columns) if all(number(r[i]) is not None for r in rows)]
    if not numeric:
        raise ValueError("至少需要一列全部为有限数值的数据")
    return {"columns": columns, "rows": rows, "numeric": numeric}


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, OverflowError):
        return None


def validate_config(data, config):
    if config["x"] not in data["columns"] or config["y"] not in data["numeric"]:
        raise ValueError("请选择已有分类字段和数值字段")
    column = config["filter_column"]
    if column and column not in data["columns"]:
        raise ValueError("筛选字段不存在")
    if config["filter_value"] and not column:
        raise ValueError("请选择筛选字段")
    return config


class ChartConfig(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    kind: Literal["bar", "line"] = "bar"
    x: str = Field(max_length=120)
    y: str = Field(max_length=120)
    filter_column: str = Field(default="", max_length=120)
    filter_value: str = Field(default="", max_length=500)


class ChartRevision(ChartConfig):
    base_version: int = Field(ge=1)


class ChartConflict(ValueError):
    pass


def save_revision(store, chart, body, role="user"):
    config = validate_config(json.loads(chart["data"]), body.model_dump(exclude={"base_version"}))
    with store.transaction() as db:
        latest = db.execute("SELECT MAX(version) FROM chart_versions WHERE chart_id=?", (chart["id"],)).fetchone()[0]
        if latest != body.base_version:
            raise ChartConflict("图表已更新，请刷新后再保存")
        version = latest + 1
        db.execute("INSERT INTO chart_versions VALUES(?,?,?,?)",
                   (chart["id"], version, json.dumps(config, ensure_ascii=False), now()))
        db.execute("INSERT INTO messages VALUES(?,?,?,?,?)",
                   (uid(), chart["task_id"], role, f"修改图表：{config['title']}（版本 {version}）；"
                    + ("折线图" if config["kind"] == "line" else "柱状图")
                    + f"；分类：{config['x']}；数值：{config['y']}；筛选："
                    + (f"{config['filter_column']} = {config['filter_value']}" if config["filter_column"] and config["filter_value"] else "全部数据"), now()))
    store.event(chart["task_id"], "chart_revised", {"chart_id": chart["id"], "version": version})
    return version
