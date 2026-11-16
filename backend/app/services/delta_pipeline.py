"""增量采集共享处理模块。

所有增量入口（扫描、补数、暂停恢复、回退、失败窗口重试）都必须经过这里，
避免重复判断、水位推进和批次生成各写一份：

* 确认水位（``确认水位``）是已经对下游承诺过的位置，只允许单调前移；
* 扫描位置（``扫描位置``）是本次扫描/重试实际停在的位置，可以后退，永远不覆盖确认水位；
* 批次以「游标编号 + 去重窗口」为唯一键，同一窗口重复提交只复用同一个批次结果；
* 记录以「游标编号 + 记录编号」为全局去重键，同一去重窗口不会重复入账；
* 早于确认水位的迟到记录进入冲突隔离，恰在水位的记录按重复处理，晚于水位的记录才推进水位。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable

from app.store import store

MODULE = "delta"
BATCH_MODULE = "delta_batch"
AUDIT_MODULE = "delta_audit"
RECORD_MODULE = "delta_record"

WATERMARK_FORMAT = "%Y-%m-%d %H:%M"
SUPPORTED_FORMATS = (
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
)

STATUS_PUBLISHED = "已发布"
STATUS_AUDIT = "待审计"
STATUS_FAILED = "失败"
PUBLISHABLE_STATUSES = (STATUS_PUBLISHED, STATUS_AUDIT)

OUTCOME_INGESTED = "入账"
OUTCOME_DUPLICATE = "重复"
OUTCOME_CONFLICT = "冲突隔离"
CLASS_LATE = "早于水位"
CLASS_AT = "恰在水位"
CLASS_AFTER = "晚于水位"

AUDIT_FAILED = "失败窗口"
AUDIT_CONFLICT = "水位冲突"
AUDIT_PENDING = "待处理"
AUDIT_DONE = "已处理"


def parse_moment(value: Any) -> datetime | None:
    """把页面/接口里的时间文本解析成时间点；空值返回 None。"""
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in SUPPORTED_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"时间「{text}」不是可识别的格式，应为 YYYY-MM-DD HH:MM")


def moment_text(value: datetime) -> str:
    return value.strftime(WATERMARK_FORMAT)


def normalize_window(
    entry: dict[str, Any],
    window_start: Any = None,
    window_end: Any = None,
) -> tuple[str, str]:
    """补齐窗口边界：缺开始取扫描位置，缺结束按去重窗口顺延。"""
    start_dt = parse_moment(window_start) or parse_moment(entry.get("扫描位置"))
    if start_dt is None:
        start_dt = parse_moment(entry.get("确认水位"))
    end_dt = parse_moment(window_end)
    if end_dt is None and start_dt is not None:
        end_dt = start_dt + timedelta(minutes=dedup_minutes(entry))
    if start_dt is None or end_dt is None:
        raise ValueError("扫描窗口缺少开始或结束位置")
    if end_dt <= start_dt:
        raise ValueError("扫描窗口结束位置必须晚于开始位置")
    return moment_text(start_dt), moment_text(end_dt)


def dedup_minutes(entry: dict[str, Any]) -> int:
    """把「15分钟 / 15m / 15」统一成分钟数，默认 15。"""
    raw = str(entry.get("去重窗口") or "").strip().lower()
    digits = "".join(ch for ch in raw if ch.isdigit())
    return int(digits) if digits else 15


def window_text(window_start: str, window_end: str) -> str:
    return f"{window_start}~{window_end}"


def split_window(value: str) -> tuple[str, str]:
    """拆回「开始~结束」；兼容旧数据的「开始 - 结束」写法。"""
    for separator in ("~", " - ", "-"):
        if separator in value:
            left, right = value.split(separator, 1)
            start = parse_moment(left)
            end = parse_moment(right)
            if start is not None and end is not None:
                return moment_text(start), moment_text(end)
    raise ValueError(f"去重窗口「{value}」不是可识别的范围")


def batch_key(cursor_code: str, window_start: str, window_end: str) -> str:
    return f"{cursor_code}|{window_text(window_start, window_end)}"


def record_key(record: dict[str, Any]) -> str:
    return str(
        record.get("记录编号")
        or record.get("业务主键")
        or record.get("id")
        or ""
    ).strip()


def classify_record(record_moment: datetime, watermark: datetime | None) -> str:
    """统一的三类判断：早于、恰在、晚于确认水位。"""
    if watermark is None:
        return CLASS_AFTER
    if record_moment < watermark:
        return CLASS_LATE
    if record_moment == watermark:
        return CLASS_AT
    return CLASS_AFTER


def find_batch(cursor_code: str, key: str) -> dict[str, Any] | None:
    """同一游标 + 同一去重窗口只能有一个批次结果。"""
    for row in store.rows(BATCH_MODULE):
        if row.get("游标编号") != cursor_code:
            continue
        if row.get("窗口唯一键") == key:
            return row
    return None


def _batch_ledger(batch: dict[str, Any]) -> set[str]:
    keys = batch.get("已入账键")
    if isinstance(keys, list):
        return {str(item) for item in keys}
    return {
        str(item.get("记录编号") or "")
        for item in batch.get("入账记录", [])
        if isinstance(item, dict)
    }


def _batch_conflicts(batch: dict[str, Any]) -> set[str]:
    return {str(item) for item in batch.get("冲突键", []) if str(item)}


def _batch_seen(batch: dict[str, Any]) -> set[str]:
    return {str(item) for item in batch.get("窗口记录键", []) if str(item)}


def _batch_duplicates(batch: dict[str, Any]) -> set[str]:
    return {str(item) for item in batch.get("重复键", []) if str(item)}


def has_record(cursor_code: str, key: str, *, skip_batch_id: int | None = None) -> bool:
    """全局去重判断：任何已入账批次里出现过，就视为重复记录。"""
    for batch in store.rows(BATCH_MODULE):
        if batch.get("游标编号") != cursor_code:
            continue
        if skip_batch_id is not None and int(batch.get("id", 0)) == skip_batch_id:
            continue
        if key in _batch_ledger(batch):
            return True
    return False


def advance_watermark(entry: dict[str, Any], candidate: Any) -> bool:
    """只前进不后退：旧水位更高时保持不动，扫描位置无权改写它。"""
    candidate_dt = parse_moment(candidate)
    if candidate_dt is None:
        return False
    current_dt = parse_moment(entry.get("确认水位"))
    if current_dt is not None and candidate_dt <= current_dt:
        return False
    entry["确认水位"] = moment_text(candidate_dt)
    return True


def move_scan_position(entry: dict[str, Any], position: Any) -> None:
    """扫描位置单独落库，允许在补数/重试时后退。"""
    position_dt = parse_moment(position)
    if position_dt is not None:
        entry["扫描位置"] = moment_text(position_dt)


def _new_batch(
    cursor_code: str,
    window_start: str,
    window_end: str,
    scan_position: str,
    key: str,
    source: str,
) -> dict[str, Any]:
    rows = store.rows(BATCH_MODULE)
    batch = {
        "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
        "status": STATUS_PUBLISHED,
        "pending": False,
        "abnormal": False,
        "批次号": f"{cursor_code}-{window_start[:10].replace('-', '')}-{window_start[11:].replace(':', '')}",
        "游标编号": cursor_code,
        "去重窗口": window_text(window_start, window_end),
        "窗口唯一键": key,
        "窗口开始": window_start,
        "窗口结束": window_end,
        "扫描位置": scan_position,
        "来源": source,
        "记录数": 0,
        "入账数": 0,
        "重复数": 0,
        "冲突数": 0,
        "终态": STATUS_PUBLISHED,
        "失败原因": "",
        "入账记录": [],
        "已入账键": [],
        "冲突键": [],
        "重复键": [],
        "窗口记录键": [],
        "确认水位": "",
    }
    rows.append(batch)
    return batch


def enqueue_audit(
    cursor_code: str,
    batch: dict[str, Any],
    audit_type: str,
    window: str,
    scan_position: str,
    reason: str,
) -> dict[str, Any]:
    """把失败窗口/水位冲突登记到增量审计待处理队列，同一窗口不重复登记。"""
    audit_key = f"{cursor_code}|{window}|{audit_type}"
    for row in store.rows(AUDIT_MODULE):
        if row.get("审计键") == audit_key:
            row["批次号"] = batch.get("批次号")
            row["扫描位置"] = scan_position
            row["原因"] = reason
            if audit_type == AUDIT_FAILED:
                row["状态"] = AUDIT_PENDING
                row["pending"] = True
                row["abnormal"] = True
            return row
    rows = store.rows(AUDIT_MODULE)
    audit = {
        "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
        "status": AUDIT_PENDING,
        "pending": True,
        "abnormal": True,
        "审计键": audit_key,
        "游标编号": cursor_code,
        "批次号": batch.get("批次号"),
        "类型": audit_type,
        "失败窗口": window,
        "扫描位置": scan_position,
        "状态": AUDIT_PENDING,
        "原因": reason,
    }
    rows.append(audit)
    return audit


def _close_audit(cursor_code: str, window: str, audit_type: str) -> None:
    audit_key = f"{cursor_code}|{window}|{audit_type}"
    for row in store.rows(AUDIT_MODULE):
        if row.get("审计键") == audit_key:
            row["状态"] = AUDIT_DONE
            row["pending"] = False
            row["abnormal"] = False


def records_in_window(entry: dict[str, Any], window_start: str, window_end: str) -> list[dict[str, Any]]:
    """从增量批次数据集里取出落在窗口内的原始记录，失败窗口重试也走这里。"""
    cursor_code = str(entry.get("游标编号") or "")
    start_dt = parse_moment(window_start)
    end_dt = parse_moment(window_end)
    if start_dt is None or end_dt is None:
        raise ValueError("失败窗口缺少可识别的开始或结束时间")
    result: list[dict[str, Any]] = []
    for row in store.rows(RECORD_MODULE):
        if row.get("游标编号") != cursor_code:
            continue
        record_dt = parse_moment(row.get("记录时间"))
        if record_dt is not None and start_dt <= record_dt <= end_dt:
            result.append(row)
    return result


def process_window(
    entry: dict[str, Any],
    window_start: str,
    window_end: str,
    records: Iterable[dict[str, Any]] | None = None,
    *,
    scan_position: Any = None,
    interrupt: bool = False,
    source: str = "扫描",
) -> dict[str, Any]:
    """统一处理一个去重窗口。

    这个函数是扫描、补数、恢复和失败重试的唯一出口：批次唯一、记录去重、
    水位单调都在这里收口。``interrupt`` 用于模拟扫描中断，只登记失败窗口，
    不写入任何记录，也不推进确认水位。
    """
    cursor_code = str(entry.get("游标编号") or "").strip()
    if not cursor_code:
        raise ValueError("增量游标缺少游标编号")
    start_text, end_text = normalize_window(entry, window_start, window_end)
    position_text = moment_text(parse_moment(scan_position) or parse_moment(end_text))
    key = batch_key(cursor_code, start_text, end_text)

    batch = find_batch(cursor_code, key)
    if batch is None:
        batch = _new_batch(cursor_code, start_text, end_text, position_text, key, source)
    else:
        batch["来源"] = source
        batch["扫描位置"] = position_text
        batch["窗口开始"] = start_text
        batch["窗口结束"] = end_text

    move_scan_position(entry, position_text)

    if interrupt:
        if batch["终态"] == STATUS_PUBLISHED and batch.get("记录数"):
            # 已发布的批次是终态结果，重复扫描不能把它改回失败。
            batch["失败原因"] = ""
            return batch
        batch["终态"] = STATUS_FAILED
        batch["失败原因"] = "扫描中断"
        batch["pending"] = True
        batch["abnormal"] = True
        batch["status"] = STATUS_FAILED
        batch["确认水位"] = str(entry.get("确认水位") or "")
        batch["处理结果"] = []
        enqueue_audit(
            cursor_code,
            batch,
            AUDIT_FAILED,
            window_text(start_text, end_text),
            position_text,
            "扫描中断",
        )
        return batch

    batch_records = list(records) if records is not None else []
    seen = _batch_seen(batch)
    ledger = _batch_ledger(batch)
    conflicts = _batch_conflicts(batch)
    duplicates = _batch_duplicates(batch)
    ingested_entries = list(batch.get("入账记录", []))
    watermark_dt = parse_moment(entry.get("确认水位"))
    candidate_watermark: datetime | None = None
    outcomes: list[dict[str, Any]] = []

    for record in batch_records:
        key_text = record_key(record)
        if not key_text:
            continue
        record_dt = parse_moment(record.get("记录时间") or record.get("时间"))
        if record_dt is None:
            continue
        if key_text in conflicts:
            outcomes.append({"记录编号": key_text, "结果": OUTCOME_CONFLICT})
            continue
        if (
            key_text in ledger
            or key_text in seen
            or has_record(cursor_code, key_text, skip_batch_id=int(batch.get("id", 0)))
        ):
            duplicates.add(key_text)
            outcomes.append({"记录编号": key_text, "结果": OUTCOME_DUPLICATE})
            continue
        seen.add(key_text)
        classification = classify_record(record_dt, watermark_dt)
        if classification == CLASS_LATE:
            conflicts.add(key_text)
            outcomes.append(
                {
                    "记录编号": key_text,
                    "记录时间": moment_text(record_dt),
                    "结果": OUTCOME_CONFLICT,
                    "判断": classification,
                }
            )
            continue
        if classification == CLASS_AT:
            duplicates.add(key_text)
            outcomes.append(
                {
                    "记录编号": key_text,
                    "记录时间": moment_text(record_dt),
                    "结果": OUTCOME_DUPLICATE,
                    "判断": classification,
                }
            )
            continue
        ledger.add(key_text)
        ingested_entries.append(
            {"记录编号": key_text, "记录时间": moment_text(record_dt)}
        )
        if candidate_watermark is None or record_dt > candidate_watermark:
            candidate_watermark = record_dt
        outcomes.append(
            {
                "记录编号": key_text,
                "记录时间": moment_text(record_dt),
                "结果": OUTCOME_INGESTED,
                "判断": classification,
            }
        )
        record["状态"] = "入账"

    for key_text in conflicts:
        for record in batch_records:
            if record_key(record) == key_text:
                record["状态"] = "冲突隔离"

    batch["窗口记录键"] = sorted(seen)
    batch["已入账键"] = sorted(ledger)
    batch["冲突键"] = sorted(conflicts)
    batch["重复键"] = sorted(duplicates)
    batch["入账记录"] = ingested_entries
    batch["记录数"] = len(seen)
    batch["入账数"] = len(ledger)
    batch["重复数"] = len(duplicates)
    batch["冲突数"] = len(conflicts)
    batch["失败原因"] = ""

    if candidate_watermark is not None:
        advance_watermark(entry, candidate_watermark)

    if conflicts:
        batch["终态"] = STATUS_AUDIT
        batch["status"] = STATUS_AUDIT
        batch["pending"] = True
        batch["abnormal"] = True
        first_conflict = sorted(conflicts)[0]
        enqueue_audit(
            cursor_code,
            batch,
            AUDIT_CONFLICT,
            window_text(start_text, end_text),
            position_text,
            f"迟到记录 {first_conflict} 早于确认水位 {entry.get('确认水位') or '空'}，已隔离不推进水位",
        )
    else:
        batch["终态"] = STATUS_PUBLISHED
        batch["status"] = STATUS_PUBLISHED
        batch["pending"] = False
        batch["abnormal"] = False
        _close_audit(cursor_code, window_text(start_text, end_text), AUDIT_FAILED)
        _close_audit(cursor_code, window_text(start_text, end_text), AUDIT_CONFLICT)

    batch["确认水位"] = str(entry.get("确认水位") or "")
    batch["处理结果"] = outcomes
    return batch


def retry_failed_window(entry: dict[str, Any], batch: dict[str, Any]) -> dict[str, Any]:
    """从失败窗口原样重试：批次唯一键不变，确认水位只可能前进。"""
    start_text, end_text = split_window(str(batch.get("去重窗口") or ""))
    records = records_in_window(entry, start_text, end_text)
    result = process_window(
        entry,
        start_text,
        end_text,
        records,
        scan_position=batch.get("扫描位置") or end_text,
        source="失败窗口重试",
    )
    result["重试"] = True
    return result


def rewind_scan_position(entry: dict[str, Any], steps: int | None = None) -> tuple[str, str]:
    """回退扫描位置；确认水位原样保留，绝不被扫描位置覆盖。"""
    step_count = steps if steps is not None else int(entry.get("回退步数") or 1)
    step_count = max(step_count, 1)
    current = parse_moment(entry.get("扫描位置")) or parse_moment(entry.get("确认水位"))
    if current is None:
        raise ValueError("游标还没有扫描位置，无法回退")
    target = current - timedelta(minutes=dedup_minutes(entry) * step_count)
    move_scan_position(entry, target)
    return moment_text(target), str(entry.get("确认水位") or "")


def pause_entry(entry: dict[str, Any]) -> None:
    entry["status"] = "已暂停"
    entry["pending"] = True


def resume_entry(entry: dict[str, Any]) -> str:
    """恢复增量：从扫描位置继续，不触碰确认水位。"""
    if not entry.get("扫描位置") and entry.get("确认水位"):
        move_scan_position(entry, entry.get("确认水位"))
    entry["status"] = "运行中"
    entry["pending"] = False
    return str(entry.get("扫描位置") or "")


def downstream_snapshot(entry: dict[str, Any]) -> dict[str, Any]:
    """下游视图：只暴露已发布/待审计批次里的入账记录，水位取确认水位且不倒退。"""
    cursor_code = str(entry.get("游标编号") or "")
    batches = [
        row
        for row in store.rows(BATCH_MODULE)
        if row.get("游标编号") == cursor_code and row.get("终态") in PUBLISHABLE_STATUSES
    ]
    records: dict[str, dict[str, Any]] = {}
    for batch in sorted(batches, key=lambda row: str(row.get("窗口开始") or "")):
        for record in batch.get("入账记录", []):
            if not isinstance(record, dict):
                continue
            key_text = str(record.get("记录编号") or "")
            if key_text:
                records[key_text] = record

    watermark = entry.get("确认水位") or ""
    watermark_dt = parse_moment(watermark)
    for batch in batches:
        batch_dt = parse_moment(batch.get("确认水位"))
        if batch_dt is not None and (watermark_dt is None or batch_dt > watermark_dt):
            watermark = moment_text(batch_dt)
            watermark_dt = batch_dt

    return {
        "游标编号": cursor_code,
        "确认水位": watermark,
        "扫描位置": entry.get("扫描位置") or "",
        "批次数": len(batches),
        "记录数": len(records),
        "记录": [records[key] for key in sorted(records)],
    }
