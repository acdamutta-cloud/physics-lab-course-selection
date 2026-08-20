# 物理实验中心智能排课选课系统

面向物理实验教学场景的排课、选课与教学资源管理系统。项目由 Vue 3 前端、
FastAPI 后端、PostgreSQL/pgvector、Redis、OR-Tools 和基于 Qwen 的 AI 对话能力组成。

仓库提供 Docker Compose 部署入口、完整 Alembic 数据库迁移、首个管理员创建工具，
以及 RAG 知识源同步和验证脚本。业务数据库和 Redis 运行数据不会提交到版本库，
目标环境会从迁移与公开知识源重新构建。

## 主要能力

- 实验课程、实验项目、培养方案和教学任务管理；
- 实验室、设备库存、设备资产和教师资格管理；
- 基于 OR-Tools 的排课与课表版本管理；
- 学生选课、退选、调整申请与审批流程；
- 教师调课和资源异常处置流程；
- 学生端 AI 咨询及系统操作指南 RAG 检索；
- 通知、审计、缓存、并发控制和数据库迁移。

## 技术结构

```text
浏览器
  └─ Vue 3 / Vite
       └─ FastAPI
            ├─ PostgreSQL 16 + pgvector（正式业务数据）
            ├─ Redis 7（缓存、锁、队列和临时状态）
            ├─ OR-Tools（排课求解）
            └─ Qwen3-14B + BAAI/bge-m3（对话与可选向量检索）
```

详细说明见 [系统架构](docs/architecture.md) 和 [RAG 知识库](docs/rag.md)。

## 使用 Docker Compose 部署

### 1. 环境要求

- Git；
- Docker Engine 或 Docker Desktop；
- Docker Compose V2；
- 能够访问所选 AI/Embedding 服务的网络。

不需要在宿主机单独安装 Python、Node.js、PostgreSQL 或 Redis。

### 2. 创建部署配置

Windows PowerShell：

```powershell
Copy-Item backend/.env.example backend/.env
```

Linux/macOS：

```bash
cp backend/.env.example backend/.env
```

编辑 `backend/.env`，至少填写：

```dotenv
POSTGRES_PASSWORD=<数据库强密码>
JWT_SECRET_KEY=<强随机密钥>
MODEL_PROVIDER=dashscope
DASHSCOPE_API_KEY=<阿里云百炼 API Key>
DASHSCOPE_MODEL=qwen3-14b
```

`backend/.env` 已被 Git 和 Docker 构建上下文排除，不得提交真实密钥。

### 3. 构建并启动

```powershell
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

应用容器启动前会执行 `alembic upgrade head`，从空 PostgreSQL 创建全部表、
约束和索引。Redis 可从空状态启动，不需要上传缓存文件。

### 4. 创建首个管理员

```powershell
docker compose exec app python -m scripts.create_admin --login-name admin
```

密码通过交互终端输入，不会作为命令行参数保存。已存在的管理员不会被覆盖。

如果目标是独立演示环境，可在创建管理员后显式写入一组最小虚构资料：

```powershell
docker compose exec app python -m scripts.bootstrap_demo --confirm-demo-data
```

该命令不会由生产启动流程自动执行，并且会拒绝向已有机构或教学资料的数据库写入。

### 5. 验证和访问

Windows：

```powershell
./scripts/verify-deployment.ps1
```

Linux/macOS：

```bash
bash scripts/verify-deployment.sh
```

- Web：<http://localhost:8000/>
- 健康检查：<http://localhost:8000/api/health>
- API 文档：<http://localhost:8000/docs>

完整步骤、升级方法和国内镜像配置见 [部署手册](docs/deployment.md)。

## RAG 知识库

系统操作指南的公开知识源位于
[`backend/app/data/student_operation_guides.py`](backend/app/data/student_operation_guides.py)。
未配置 Embedding 服务时使用 BM25；配置后可同步到 pgvector 并使用混合检索：

```powershell
docker compose exec app python -m scripts.sync_operation_guides
docker compose exec app python -m scripts.verify_operation_guide_search
```

向量索引属于可重新生成的派生数据，不提交到 GitHub。详见 [RAG 说明](docs/rag.md)。

## 本地开发与测试

后端开发说明见 [backend/README.md](backend/README.md)。常用检查：

```powershell
cd backend
python -m pip install -r requirements-dev.txt
python -m pytest tests/unit -q

cd ../frontend
npm ci
npm run build
```

## 数据与安全

- PostgreSQL 是正式业务数据来源，Redis 只保存可重建状态；
- 不提交 `.env`、数据库备份、日志、真实账号或真实师生数据；
- 正式部署不内置默认账号和默认密码；
- 备份、恢复和删除数据卷前应先阅读 [备份与恢复](docs/backup-restore.md)；
- 安全问题请参阅 [SECURITY.md](SECURITY.md)。

## 参与开发

请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可状态

本仓库尚未声明开源许可证。查看源代码不等于获得复制、修改或分发授权；
维护者选择许可证后应在仓库根目录补充 `LICENSE`。
