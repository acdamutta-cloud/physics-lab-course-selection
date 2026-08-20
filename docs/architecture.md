# 系统架构

## 组件边界

- `frontend/`：Vue 3 单页应用，生产构建产物由 FastAPI 托管；
- `backend/app/api/`：HTTP API 与认证依赖；
- `backend/app/services/`：排课、选课、调课、资源和咨询业务服务；
- `backend/app/scheduler/`：OR-Tools 排课输入、约束、目标和结果解析；
- `backend/app/agents/`：AI 对话图、工具、提示词和模型供应方适配；
- `backend/app/models/`：SQLAlchemy 模型；
- `backend/alembic/`：数据库结构及系统规则迁移；
- `backend/app/cache/`：Redis 缓存、锁和临时状态；
- `backend/evals/`：AI/RAG 评测数据与评测器。

## 数据职责

PostgreSQL 保存账号、教学基础资料、课表、选课、审批、资源和审计等正式数据。
数据库结构由 Alembic 从空库创建，不能通过上传本地数据目录进行部署。

Redis 保存缓存、选课队列、库存镜像、并发锁和校验令牌。Redis 数据丢失不应导致
PostgreSQL 正式业务数据丢失；缓存和派生状态应通过应用预热或运维脚本恢复。

RAG 的知识源保存在代码仓库，pgvector 表保存可重新生成的向量索引。

## 部署拓扑

根目录 `docker-compose.yml` 提供单机部署拓扑：

```text
physics-lab-app :8000
  ├─ physics-lab-postgres :5432（仅 Compose 内部访问）
  └─ physics-lab-redis :6379（仅 Compose 内部访问）
```

根 Compose 不向宿主机暴露 PostgreSQL 和 Redis 端口。开发基础设施编排位于
`backend/docker-compose.yml`，它会开放数据库和 Redis 端口，仅适合本地开发。

## 初始化边界

所有表结构、约束和索引必须通过 `alembic upgrade head` 初始化。生产部署只创建首个
管理员和系统必需的规则数据，不预填排课、选课、审批、通知或审计过程记录。

学校、专业、班级、学期、课程、实验项目、实验室、教师和学生属于机构业务资料，
应由运维或系统管理员按正式来源录入或导入，而不是固化在公开迁移中。

独立演示环境可显式运行 `scripts.bootstrap_demo` 写入一组最小虚构资料。该脚本不是
生产启动步骤，并会拒绝与已有机构或教学资料混合。
