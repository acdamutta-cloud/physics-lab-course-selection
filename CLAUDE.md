# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

物理实验中心智能排课选课智能体系统。基于 FastAPI + LangGraph + OR-Tools 的多 Agent 排课系统，处理初始排课、学生选课、运行调整三大业务场景。

## 常用命令

### 后端 (Python 3.12, `backend/` 目录下执行)

```bash
# 安装依赖
pip install -r requirements-dev.txt

# 启动基础设施 (PostgreSQL 16 + Redis 7)
docker compose up -d postgres redis

# 初始化数据库（首次运行）
python -m scripts.init_database
alembic upgrade head

# 启动后端（开发中，main.py 尚未实现路由）
uvicorn app.main:app --reload --port 8000

# 代码检查
ruff check .
mypy app/

# 测试
pytest
pytest --cov=app --cov-report=term-missing
```

### 前端 (TypeScript + Vue 3 + Vite, `frontend/` 目录下执行)

```bash
npm install
npm run dev        # 开发服务器 http://localhost:5173
npm run build      # 生产构建 (vue-tsc 类型检查 + vite build)
npm run preview    # 预���构建产物
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
    → PostgreSQL (业务数据) + Redis (缓存/锁/临时状态)
```

### 核心设计原则

**"确定性核心 + AI 交互"**：Agent 只负责理解需求、规划工具调用、解释结果、发起确认。所有关键决策（约束检查、容量验证、排课求解、事务执行）由确定性 Python 服务完成。

Agent **不得**：直接执行 SQL、修改业务数据、自行判断冲突/容量/资格、用模型推理替代规则引擎、绕过审批发布课表。

所有写操作必须经过：权限校验 → 参数校验 → 规则校验 → 操作预览 → 人工确认(`interrupt()`) → 幂等键 → 事务 → 审计日志。

### 6 个 Agent + 3 个子图

| Agent | 职责 |
|---|---|
| Supervisor | 意图识别，路由到子图 |
| Student Advisor | 学生咨询、选课、申请 |
| Scheduling | 管理员排课请求，求解器编排 |
| Adjustment | 运行调课、影响分析 |
| Validation | 独立验证排课结果 |
| Explanation | 解释求解结果、方案对比 |

3 个 LangGraph 子图：`student_graph` / `scheduling_graph` / `adjustment_graph`，由 `main_graph` 统一路由。

### 数据库 (41 张表)

模型分为 12 个模块：`identity`(用户/学生/教师), `curriculum`(课程/项目/培养方案), `resources`(实验室/设备), `scheduling`(教学任务/排课版本/实验场次), `enrollment`(选课窗口/记录), `rules`(规则集), `application`(申请/审批), `agent`(运行日志), `audit`(操作日志), `notification`(通知)。

### 时间槽约定

每天 12 节课，分为 3 个时段：上午(1-4节)、下午(5-8节)、晚间(9-12节)。每个实验场次占用一个时段（4 节课）。

### 关键配置文件

- `backend/app/core/config/settings.py` — 所有环境变量 (DB/JWT/DeepSeek/CORS)
- `backend/app/db/session.py` — AsyncSession 工厂
- `backend/app/db/langgraph_checkpoint.py` — LangGraph PostgreSQL checkpointer
- `backend/app/models/base.py` — Base, UUIDPrimaryKeyMixin, TimestampMixin, AuditMixin

## 开发状态

项目处于早期阶段：
- **已完成**：数据库模型 (41 张表)、Alembic migration、种子数据脚本、LangGraph checkpoint、6 个 Agent 的 v1 提示词、配置/基础设施、前端可交互原型
- **空桩**：所有 API 路由、Service、CRUD、Schema、Rules Engine、OR-Tools Scheduler、LangGraph StateGraph 实现
- **未开始**：测试、Dockerfile、CI/CD

## 详细设计文档

- `.agents/agents.md` — 多 Agent 架构完整规范（1567 行），是 agent 实现的最重要参考
- `【V1.0.0】物理实验中心智能排课选课智能体系统.md` — 完整 PRD
- `docs/业务数据库字段范例.md` — 数据库字段说明
- `backend/README.md` — 后端环境搭建详细步骤
