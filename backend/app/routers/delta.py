"""增量采集接口：维护增量游标，覆盖提交校验、启动增量、暂停增量等动作。

批次、审计队列与下游视图的读接口也在这一组，方便批次页复显扫描位置。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, BatchResult, EntryPayload, PageResult
from app.services.delta import DeltaService

router = APIRouter(prefix="/api/delta", tags=["增量采集"])

service = DeltaService()

LIST_FIELDS = [
    "游标编号",
    "目标端点",
    "增量字段",
    "确认水位",
    "扫描位置",
    "回退步数",
    "批次上限",
    "去重窗口",
    "重跑开关",
]
STATUSES = ["草稿", "待校验", "运行中", "已暂停"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按游标编号检索"),
    status: str | None = Query(default=None, description="草稿、待校验、运行中、已暂停"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按游标编号与状态过滤增量采集列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出增量采集清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "delta", "total": total, "items": items}


@router.get("/batches", response_model=PageResult[dict])
def list_batches(
    cursor_code: str | None = Query(default=None, description="按游标编号过滤"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """增量批次数据集：每个游标 + 去重窗口只对应一个批次，复显扫描位置。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_batches(cursor_code=cursor_code, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/batches/{batch_id}", response_model=dict)
def get_batch(batch_id: int) -> dict:
    """批次详情：显示扫描位置、窗口和入账明细。"""
    batch = service.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"增量批次 {batch_id} 不存在或已归档")
    return batch


@router.post("/batches/{batch_id}/retry", response_model=BatchResult)
def retry_batch(batch_id: int) -> BatchResult:
    """从失败窗口重试；确认水位只能前进，不能回退。"""
    entry, batch, message = service.retry_batch(batch_id)
    if entry is None or batch is None:
        return BatchResult(ok=False, message=message)
    return BatchResult(ok=True, message=message, entry=entry, batch=batch)


@router.get("/audit", response_model=PageResult[dict])
def list_audit(
    status: str | None = Query(default=None, description="待处理、已处理"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """增量审计待处理队列：失败窗口与水位冲突都登记在这里。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_audit(status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条增量游标明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"增量游标 {entry_id} 不存在或已归档")
    return entry


@router.get("/{entry_id}/downstream", response_model=dict)
def get_downstream(entry_id: int) -> dict:
    """下游视图：确认水位不倒退，已入账记录不重复。"""
    snapshot = service.downstream(entry_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"增量游标 {entry_id} 不存在或已归档")
    return snapshot


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条增量游标，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        if any("已存在" in item for item in missing):
            return ActionResult(ok=False, message=missing[0])
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="增量游标已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条增量游标执行提交校验、启动增量、暂停增量；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/scan", response_model=BatchResult)
def scan_entry(entry_id: int, payload: EntryPayload) -> BatchResult:
    """扫描一个去重窗口并生成唯一批次；中断时登记失败窗口。"""
    entry, batch, message = service.scan(entry_id, payload.values)
    if entry is None or batch is None:
        return BatchResult(ok=False, message=message)
    return BatchResult(ok=True, message=message, entry=entry, batch=batch)


@router.post("/{entry_id}/rewind", response_model=ActionResult)
def rewind_entry(entry_id: int) -> ActionResult:
    """回退扫描位置，确认水位保持不变。"""
    entry, message = service.rewind(entry_id)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
