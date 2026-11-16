# API数据聚合采集中心

面向多源API接入、接口编排、数据聚合、响应校验、频率控制与数据转储的API数据聚合采集中心。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 接口管理 | `endpoint` | API端点 | 端点编号、接口地址、请求方法 |
| 接口编排 | `chain` | 调用链路 | 链路编号、端点序列、数据传递映射 |
| 频率控制 | `freq` | 频率规则 | 规则编号、目标端点、每秒上限 |
| 数据映射 | `mapper` | 映射规则 | 规则编号、源字段路径、目标字段名 |
| 响应校验 | `validator` | 校验规则 | 规则编号、关联端点、校验字段 |
| 失败重试 | `retry` | 重试策略 | 策略编号、关联端点、最大重试 |
| 数据聚合 | `aggregator` | 聚合任务 | 任务编号、输入端点列表、合并策略 |
| 凭证管理 | `auth` | 认证凭证 | 凭证编号、关联端点、认证类型 |
| 缓存策略 | `cache` | 缓存规则 | 规则编号、关联端点、缓存键模板 |
| 数据转储 | `dump` | 转储配置 | 配置编号、数据来源、存储类型 |
| 采集调度 | `schedule` | 调度计划 | 计划编号、关联链路、调度表达式 |
| 采集监控 | `monitor` | 监控指标 | 指标编号、关联端点、成功率阈值 |
| 数据转换 | `transform` | 转换脚本 | 脚本编号、输入数据源、转换表达式 |
| 数据清洗 | `cleanse` | 清洗规则 | 规则编号、目标字段、空值替换 |
| 限流配置 | `ratelimit` | 限流规则 | 规则编号、目标端点、令牌速率 |
| 采集日志 | `log` | 日志记录 | 记录编号、关联端点、请求时间 |
| 增量采集 | `delta` | 增量游标 | 游标编号、目标端点、增量字段、确认水位、扫描位置 |
| 数据导出 | `export` | 导出通道 | 通道编号、输出数据集、目标系统 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 增量采集的水位与批次

增量采集的重复判断、水位推进和批次生成统一收在
`backend/app/services/delta_pipeline.py`，扫描、补数、暂停恢复、回退和失败窗口重试
都调同一个 `process_window`，不再各自实现一套：

- 确认水位与扫描位置分开保存：确认水位只前进不后退，扫描位置可以回退，
  扫描位置永远不覆盖确认水位。
- 批次以「游标编号 + 去重窗口」为唯一键，同一窗口重复扫描只复用同一个批次结果；
  记录以「游标编号 + 记录编号」去重，同一去重窗口不会重复入账。
- 早于确认水位的迟到记录记冲突、进增量审计待处理队列；恰在水位的记录按重复处理；
  晚于水位的记录才入账并推进确认水位。
- 扫描中断只登记失败窗口，不写记录、不推水位；从失败窗口重试时窗口与批次不变。

相关接口：

```text
GET  /api/delta/batches                  批次数据集（复显扫描位置）
GET  /api/delta/batches/{id}             批次详情
POST /api/delta/batches/{id}/retry       从失败窗口重试
GET  /api/delta/audit                    增量审计待处理队列
GET  /api/delta/{id}/downstream          下游视图（水位不倒退、记录不重复）
POST /api/delta/{id}/scan                扫描一个去重窗口
POST /api/delta/{id}/rewind              回退扫描位置
```

回归用例：`cd backend && .venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m unittest discover -s tests -v`。

领域：数据工程
子方向：数据采集、爬虫与RPA
