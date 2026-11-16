"""增量采集业务规则：状态流转、字段校验与筛选口径都收在这里。

重复判断、水位推进与批次生成统一委托给 ``app.services.delta_pipeline``，
本模块只保留游标自身的登记、查询和动作分发，不再各自实现一套去重逻辑。
"""
from __future__ import annotations

from typing import Any

from app.services import delta_pipeline
from app.store import store

MODULE = "delta"
REQUIRED_FIELDS = ["游标编号", "目标端点", "增量字段"]
STATUS_ORDER = ["草稿", "待校验", "运行中", "已暂停"]
ACTION_RULES = {"提交校验": "待校验", "启动增量": "运行中", "暂停增量": "已暂停"}
NEGATIVE_ACTIONS = ["暂停增量"]


class DeltaService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("游标编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def get_entry_by_code(self, cursor_code: str) -> dict[str, Any] | None:
        for row in store.rows(MODULE):
            if row.get("游标编号") == cursor_code:
                return row
        return None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        cursor_code = str(values.get("游标编号") or "").strip()
        if self.get_entry_by_code(cursor_code) is not None:
            return None, [f"游标编号 {cursor_code} 已存在"]
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["回退步数"] = values.get("回退步数") or 1
        entry["批次上限"] = values.get("批次上限") or 200
        entry["去重窗口"] = values.get("去重窗口") or "15分钟"
        entry["重跑开关"] = values.get("重跑开关") or "关闭"
        entry["确认水位"] = str(values.get("确认水位") or "").strip()
        entry["扫描位置"] = str(values.get("扫描位置") or entry["确认水位"]).strip()
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"增量游标 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于增量采集可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        if action == "暂停增量":
            delta_pipeline.pause_entry(entry)
        elif action == "启动增量":
            delta_pipeline.resume_entry(entry)
        else:
            entry["status"] = target
            entry["pending"] = True
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"增量游标已{action}"

    def scan(
        self,
        entry_id: int,
        values: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
        """手动触发一次窗口扫描；记录可来自请求体，缺省读增量批次数据集。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, None, f"增量游标 {entry_id} 不存在或已归档"
        if entry.get("status") != "运行中":
            return None, None, "增量游标未在运行中，请先启动增量"
        try:
            window_start, window_end = delta_pipeline.normalize_window(
                entry,
                values.get("窗口开始"),
                values.get("窗口结束"),
            )
            records = values.get("记录")
            if records is None:
                records = delta_pipeline.records_in_window(entry, window_start, window_end)
            batch = delta_pipeline.process_window(
                entry,
                window_start,
                window_end,
                records,
                scan_position=values.get("扫描位置") or window_end,
                interrupt=bool(values.get("中断")),
                source=str(values.get("来源") or "扫描"),
            )
        except ValueError as exc:
            return None, None, str(exc)
        return entry, batch, "增量窗口已扫描"

    def rewind(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """回退扫描位置；确认水位不变。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"增量游标 {entry_id} 不存在或已归档"
        try:
            position, watermark = delta_pipeline.rewind_scan_position(entry)
        except ValueError as exc:
            return None, str(exc)
        return entry, f"扫描位置已回退到 {position}，确认水位保持 {watermark or '空'}"

    def retry_batch(
        self,
        batch_id: int,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
        """从失败窗口重试：窗口与批次唯一键不变，确认水位不倒退。"""
        batch = store.find(delta_pipeline.BATCH_MODULE, batch_id)
        if batch is None:
            return None, None, f"增量批次 {batch_id} 不存在或已归档"
        if batch.get("终态") != delta_pipeline.STATUS_FAILED:
            return None, None, "只有失败窗口需要重试"
        entry = self.get_entry_by_code(str(batch.get("游标编号") or ""))
        if entry is None:
            return None, None, f"批次对应的游标 {batch.get('游标编号')} 不存在或已归档"
        try:
            result = delta_pipeline.retry_failed_window(entry, batch)
        except ValueError as exc:
            return None, None, str(exc)
        return entry, result, "失败窗口已重试"

    def list_batches(
        self,
        *,
        cursor_code: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(delta_pipeline.BATCH_MODULE)
        if cursor_code:
            rows = [row for row in rows if row.get("游标编号") == cursor_code]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_batch(self, batch_id: int) -> dict[str, Any] | None:
        return store.find(delta_pipeline.BATCH_MODULE, batch_id)

    def list_audit(
        self,
        *,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(delta_pipeline.AUDIT_MODULE)
        if status:
            rows = [row for row in rows if row.get("状态") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def downstream(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        return delta_pipeline.downstream_snapshot(entry)
