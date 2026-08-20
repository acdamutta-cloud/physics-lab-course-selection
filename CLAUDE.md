# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

物理实验中心智能排课选课智能体系统。基于 FastAPI + LangGraph + OR-Tools 的多 Agent 排课系统，覆盖初始排课、学生选课、运行调整、教师调课、资源管理五大业务场景。系统已实现完整业务闭环（详见下方开发状态）。

## 常用命令

### 后端 (Python 3.12, 在 `backend/` 目录下执行)

```bash
# 安装依赖
pip install -r requirements-dev.txt

# 启动基础设施 (PostgreSQL 16 + Redis 7)
docker compose up -d postgres redis

# 数据库迁移
alembic upgrade head
alembic revision --autogenerate -m "描述"

# 启动后端（必须使用 langchain_env_2 环境，且必须 cd 到 backend/ 目录）
python -m uvicorn app.main:app --reload --port 8000

# 代码检查与类型检查
ruff check .
mypy app/

# 测试
pytest
pytest --cov=app --cov-report=term-missing
```

### 前端 (TypeScript + Vue 3 + Vite, `frontend/` 目录下执行)

```bash
npm install
npm run dev        # 开发服务器 http://localhost:5173（代理 /api 到 8000）
npm run build      # 生产构建 (vue-tsc 类型检查 + vite build)
npm run preview    # 预览构建产物
```

## 核心架构

### 分层结构

```
Frontend (Vue 3 SPA, 无 Router/Pinia, v-if 切换视图)
    → FastAPI REST API (/api/v1/...)
        → Services (业务逻辑)
        → Rules Engine (硬/软约束校验)
        → OR-Tools CP-SAT Solver (排课求解)
    → LangGraph Multi-Agent (意图理解 + 结果解释 + 人工确认)
    → PostgreSQL (业务数据) + Redis (缓存/锁/队列)
    → Celery (异步任务: 导入/通知/排课)
```

### 核心设计原则

**"确定性核心 + AI 交互"**：Agent 只负责理解需求、规划工具调用、解释结果、发起确认。所有关键决策（约束检查、容量验证、排课求解、事务执行）由确定性 Python 服务完成。

Agent **不得**：直接执行 SQL、修改业务数据、自行判断冲突/容量/资格、用模型推理替代规则引擎、绕过审批发布课表。

所有写操作必须经过：权限校验 → 参数校验 → 规则校验 → 操作预览 → 人工确认(`interrupt()`) → 幂等键 → 事务 → 审计日志。

### 多 Agent 架构

- `backend/app/agents/` — Supervisor / Student Advisor / Scheduling / Adjustment / Validation / Explanation 六类 Agent
  - `nodes/` — 各 Agent 节点逻辑
  - `tools/` — Agent 工具（确定性服务封装）
  - `guardrails/` — 输入校验护栏
  - `states/` — LangGraph 状态定义
  - `prompts/` — 提示词（v2 体系，按 Agent 分目录）
  - `graphs/` — LangGraph 子图（student_graph / scheduling_graph / adjustment_graph）
  - `model_provider.py` / `registry.py` — 模型提供与 Agent 注册
- 3 个 LangGraph 子图由 `main_graph` 统一路由

### 数据库 (64 张表, 12 个模型模块)

`identity`(用户/学生/教师) / `curriculum`(课程/项目/培养方案) / `resources`(实验室/设备/资产台账) / `scheduling`(教学任务/排课版本/实验场次/课表) / `enrollment`(选课窗口/记录) / `rules`(规则集/软规则权重) / `application`(申请/审批) / `teaching_adjustment`(调课流程) / `audit`(操作日志) / `notification`(通知) / `agent`(运行日志) / `base`(公共 Mixin)

迁移文件在 `backend/alembic/versions/`，共 28 个，`alembic upgrade head` 直接到最新。

### 时间槽约定

每天 12 节课，分为 3 个时段：上午(1-4节)、下午(5-8节)、晚间(9-12节)。每个实验场次占用一个时段（4 节课）。周一~周日均为可排课日。

### 关键配置文件

- `backend/app/core/config/settings.py` — 所有环境变量 (DB/JWT/DeepSeek/Redis/Celery/CORS)
- `backend/app/db/session.py` — AsyncSession 工厂
- `backend/app/db/redis_client.py` — Redis 连接池客户端
- `backend/app/db/langgraph_checkpoint.py` — LangGraph PostgreSQL checkpointer
- `backend/app/models/base.py` — Base, UUIDPrimaryKeyMixin, TimestampMixin, AuditMixin
- `backend/app/scheduler/` — OR-Tools 求解器 (cp_sat_solver / constraints / objective / validator)

### 后端启动生命周期 (main.py lifespan)

启动时：Redis 连接预热 → 选课库存预热 → 5 个后台周期任务（选课队列消费者、auth 档案预热、学生缓存预热、选课上下文预热、资源问题逾期扫描）。Redis/DB 预热失败不会阻塞只读 API（降级策略）。

## 开发环境

- 后端运行环境：conda **langchain_env_2**（勿用 anaconda base）
- 启动后端必须 `cd backend/` 后再运行 uvicorn，否则 `No module named 'app'`
- 开发基础设施：PostgreSQL 本机 `127.0.0.1:5432`（库名 `physics_lab`）、Redis 在 VM `192.168.100.128:6379`
- 前端 Vite 代理 `/api` → `http://localhost:8000`
- 生产部署：根目录 `Dockerfile` + `docker-compose.yml`（postgres/redis/app 三服务）

## 开发状态

- **已完成**：数据库模型 (64 表) + 28 个迁移、全部 API 路由 (15 个)、Services (11 个)、CRUD、Rules Engine、OR-Tools Scheduler、LangGraph 子图与 6 Agent、Celery workers、前端三大门户 (学生/教师/系统)、选课窗口与选课队列、教师调课与资源转移流程、LLM 评估框架 (`backend/evals/`)、Docker 部署配置
- **测试**：`backend/tests/` 含 unit (25+ 文件) / integration / agents / evals / load，运行 `pytest` 全量验证
- **调试脚本**：`backend/scripts/` 存放一次性数据修复/复现脚本（不入正式代码路径）
