"""接口出入参模型：列表分页、动作结果与各模块的明细结构。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class BatchResult(ActionResult):
    """增量窗口处理结果：同时回带游标与批次，批次页据此复显扫描位置。"""

    batch: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    """登记或修改一条业务记录时提交的字段集合。"""

    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None



class EndpointEntry(BaseModel):
    """API端点明细结构。"""

    field_0: str | None = None  # 端点编号
    field_1: str | None = None  # 接口地址
    field_2: str | None = None  # 请求方法
    field_3: str | None = None  # 认证方式
    field_4: str | None = None  # 请求头配置
    field_5: str | None = None  # 参数模板
    field_6: str | None = None  # 响应格式
    field_7: str | None = None  # 超时秒数

class ChainEntry(BaseModel):
    """调用链路明细结构。"""

    field_0: str | None = None  # 链路编号
    field_1: str | None = None  # 端点序列
    field_2: str | None = None  # 数据传递映射
    field_3: str | None = None  # 条件分支
    field_4: str | None = None  # 回退策略
    field_5: str | None = None  # 最大并发
    field_6: str | None = None  # 链路超时
    field_7: str | None = None  # 补偿机制

class FreqEntry(BaseModel):
    """频率规则明细结构。"""

    field_0: str | None = None  # 规则编号
    field_1: str | None = None  # 目标端点
    field_2: str | None = None  # 每秒上限
    field_3: str | None = None  # 突发容量
    field_4: str | None = None  # 冷却秒数
    field_5: str | None = None  # 队列长度
    field_6: str | None = None  # 优先级
    field_7: str | None = None  # 并发窗口

class MapperEntry(BaseModel):
    """映射规则明细结构。"""

    field_0: str | None = None  # 规则编号
    field_1: str | None = None  # 源字段路径
    field_2: str | None = None  # 目标字段名
    field_3: str | None = None  # 类型转换
    field_4: str | None = None  # 默认值填充
    field_5: str | None = None  # 必填校验
    field_6: str | None = None  # 清洗表达式
    field_7: str | None = None  # 版本标签

class ValidatorEntry(BaseModel):
    """校验规则明细结构。"""

    field_0: str | None = None  # 规则编号
    field_1: str | None = None  # 关联端点
    field_2: str | None = None  # 校验字段
    field_3: str | None = None  # 校验类型
    field_4: str | None = None  # 期望范围
    field_5: str | None = None  # 采样比例
    field_6: str | None = None  # 阻断级别
    field_7: str | None = None  # 告警阈值

class RetryEntry(BaseModel):
    """重试策略明细结构。"""

    field_0: str | None = None  # 策略编号
    field_1: str | None = None  # 关联端点
    field_2: str | None = None  # 最大重试
    field_3: str | None = None  # 退避倍数
    field_4: str | None = None  # 初始间隔秒
    field_5: str | None = None  # 最大间隔秒
    field_6: str | None = None  # 重试条件
    field_7: str | None = None  # 降级端点

class AggregatorEntry(BaseModel):
    """聚合任务明细结构。"""

    field_0: str | None = None  # 任务编号
    field_1: str | None = None  # 输入端点列表
    field_2: str | None = None  # 合并策略
    field_3: str | None = None  # 去重键
    field_4: str | None = None  # 排序字段
    field_5: str | None = None  # 输出格式
    field_6: str | None = None  # 批次大小
    field_7: str | None = None  # 调度周期

class AuthEntry(BaseModel):
    """认证凭证明细结构。"""

    field_0: str | None = None  # 凭证编号
    field_1: str | None = None  # 关联端点
    field_2: str | None = None  # 认证类型
    field_3: str | None = None  # 凭证密钥
    field_4: str | None = None  # 轮换策略
    field_5: str | None = None  # 有效期天数
    field_6: str | None = None  # 过期告警
    field_7: str | None = None  # 自动刷新

class CacheEntry(BaseModel):
    """缓存规则明细结构。"""

    field_0: str | None = None  # 规则编号
    field_1: str | None = None  # 关联端点
    field_2: str | None = None  # 缓存键模板
    field_3: str | None = None  # 过期秒数
    field_4: str | None = None  # 预热开关
    field_5: str | None = None  # 淘汰策略
    field_6: str | None = None  # 命中率阈值
    field_7: str | None = None  # 最大条目

class DumpEntry(BaseModel):
    """转储配置明细结构。"""

    field_0: str | None = None  # 配置编号
    field_1: str | None = None  # 数据来源
    field_2: str | None = None  # 存储类型
    field_3: str | None = None  # 输出路径
    field_4: str | None = None  # 文件格式
    field_5: str | None = None  # 分区策略
    field_6: str | None = None  # 压缩开关
    field_7: str | None = None  # 保留天数

class ScheduleEntry(BaseModel):
    """调度计划明细结构。"""

    field_0: str | None = None  # 计划编号
    field_1: str | None = None  # 关联链路
    field_2: str | None = None  # 调度表达式
    field_3: str | None = None  # 时区
    field_4: str | None = None  # 最大并行数
    field_5: str | None = None  # 超时终止
    field_6: str | None = None  # 失败告警
    field_7: str | None = None  # 补偿策略

class MonitorEntry(BaseModel):
    """监控指标明细结构。"""

    field_0: str | None = None  # 指标编号
    field_1: str | None = None  # 关联端点
    field_2: str | None = None  # 成功率阈值
    field_3: str | None = None  # 延迟阈值
    field_4: str | None = None  # 错误率告警
    field_5: str | None = None  # 检测频率
    field_6: str | None = None  # 通知渠道
    field_7: str | None = None  # 静默分钟

class TransformEntry(BaseModel):
    """转换脚本明细结构。"""

    field_0: str | None = None  # 脚本编号
    field_1: str | None = None  # 输入数据源
    field_2: str | None = None  # 转换表达式
    field_3: str | None = None  # 输出结构
    field_4: str | None = None  # 依赖编排
    field_5: str | None = None  # 执行顺序
    field_6: str | None = None  # 超时秒数
    field_7: str | None = None  # 异常策略

class CleanseEntry(BaseModel):
    """清洗规则明细结构。"""

    field_0: str | None = None  # 规则编号
    field_1: str | None = None  # 目标字段
    field_2: str | None = None  # 空值替换
    field_3: str | None = None  # 格式规范化
    field_4: str | None = None  # 异常值剔除
    field_5: str | None = None  # 去重键
    field_6: str | None = None  # 规则版本
    field_7: str | None = None  # 生效日期

class RatelimitEntry(BaseModel):
    """限流规则明细结构。"""

    field_0: str | None = None  # 规则编号
    field_1: str | None = None  # 目标端点
    field_2: str | None = None  # 令牌速率
    field_3: str | None = None  # 桶容量
    field_4: str | None = None  # 等待队列
    field_5: str | None = None  # 降级策略
    field_6: str | None = None  # 优先级
    field_7: str | None = None  # 生效时段

class LogEntry(BaseModel):
    """日志记录明细结构。"""

    field_0: str | None = None  # 记录编号
    field_1: str | None = None  # 关联端点
    field_2: str | None = None  # 请求时间
    field_3: str | None = None  # 响应状态
    field_4: str | None = None  # 耗时毫秒
    field_5: str | None = None  # 返回行数
    field_6: str | None = None  # 错误信息
    field_7: str | None = None  # 重试次数

class DeltaEntry(BaseModel):
    """增量游标明细结构。"""

    field_0: str | None = None  # 游标编号
    field_1: str | None = None  # 目标端点
    field_2: str | None = None  # 增量字段
    field_3: str | None = None  # 确认水位
    field_4: str | None = None  # 回退步数
    field_5: str | None = None  # 批次上限
    field_6: str | None = None  # 去重窗口
    field_7: str | None = None  # 重跑开关
    field_8: str | None = None  # 扫描位置

class ExportEntry(BaseModel):
    """导出通道明细结构。"""

    field_0: str | None = None  # 通道编号
    field_1: str | None = None  # 输出数据集
    field_2: str | None = None  # 目标系统
    field_3: str | None = None  # 导出格式
    field_4: str | None = None  # 传输协议
    field_5: str | None = None  # 频率限制
    field_6: str | None = None  # 加密开关
    field_7: str | None = None  # 完整性校验
