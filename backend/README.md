# 物理实验智能排课系统后端

当前目录为 FastAPI + PostgreSQL + Redis + LangGraph + OR-Tools 的后端框架。
现阶段已包含初始数据库模型与模拟数据脚本，尚未实现具体业务接口。

## 1. 环境要求

- Python 3.12
- Docker Desktop 或兼容的 Docker Compose 环境
- PostgreSQL 16（推荐通过 Compose 启动）
- Redis 7（推荐通过 Compose 启动）

## 2. 创建本地环境变量

在 `backend` 目录执行：

```powershell
Copy-Item .env.example .env
```

编辑 `.env` 并注入所选聊天模型供应方的 API Key。使用阿里云百炼
Qwen3-14B 时配置：

```dotenv
MODEL_PROVIDER=dashscope
DASHSCOPE_API_KEY=
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_MODEL=qwen3-14b
DASHSCOPE_ENABLE_THINKING=false
```

如需切回 Hugging Face：

```dotenv
MODEL_PROVIDER=huggingface
HF_TOKEN=
HUGGINGFACE_BASE_URL=https://router.huggingface.co/v1
HUGGINGFACE_MODEL=Qwen/Qwen3.5-4B:featherless-ai
```

如需切回 DeepSeek：

```dotenv
DEEPSEEK_API_KEY=
MODEL_PROVIDER=deepseek
```

将真实 Key 仅填写在本地 `.env` 的等号后面。`.env` 已加入
`.gitignore`，不得提交真实 API Key。

如果暂时不调用真实模型，保持：

```dotenv
MODEL_PROVIDER=mock
DEEPSEEK_API_KEY=
HF_TOKEN=
DASHSCOPE_API_KEY=
```

## 3. 聊天模型配置说明

阿里云百炼、Hugging Face Router 和 DeepSeek 均提供 OpenAI 兼容接口，项目统一通过
`ChatOpenAI` 调用。切换供应方不会改变选课、调课、排课或审批算法。

阿里云百炼配置为：

```dotenv
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_MODEL=qwen3-14b
DASHSCOPE_ENABLE_THINKING=false
```

`DASHSCOPE_ENABLE_THINKING=false` 用于关闭思考过程输出，避免将模型推理内容展示给学生。

Hugging Face 配置为：

```dotenv
HUGGINGFACE_BASE_URL=https://router.huggingface.co/v1
HUGGINGFACE_MODEL=Qwen/Qwen3.5-4B:featherless-ai
```

DeepSeek 配置为：

项目按当前约定提供：

```dotenv
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-v4-pro
```

DeepSeek 当前官方 OpenAI 兼容示例使用：

```dotenv
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

如果调用 SDK 时出现路径兼容问题，可以只修改环境变量，无需修改代码。

## 4. 安装依赖

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
```

## 5. PostgreSQL 配置

数据库连接由以下环境变量组合生成，代码和日志均不得输出密码：

```dotenv
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_USER=postgres
POSTGRES_PASSWORD=
POSTGRES_DB=physics_lab
POSTGRES_SSLMODE=disable
```

如使用本机已经安装的 PostgreSQL，无需启动 Compose。

如使用容器，在 `.env` 中配置密码后执行：

```powershell
docker compose up -d postgres redis
docker compose ps
```

停止服务：

```powershell
docker compose stop
```

如需删除本地容器和数据卷，应先确认数据不再需要，再执行：

```powershell
docker compose down -v
```

## 6. 初始化数据库

以下命令均在 `backend` 目录执行。建库脚本只会在目标数据库不存在时创建
数据库，不会删除、清空或覆盖已有数据库。

```powershell
python -m scripts.init_database
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
python -m scripts.seed_demo_data
python -m scripts.verify_demo_data
```

模拟数据使用固定 UUID，可重复执行：

- 10 个明确标注为模拟数据的工科专业；
- 每个专业 5 个班，每班 40 人；
- 每个专业 200 人，共 2000 名模拟学生；
- 同步生成演示所需的课程、实验项目、教师、实验室、设备、规则和草稿排课数据。

如果数据库中只存在一部分模拟数据，脚本会中止并提示人工检查，不会自动
覆盖或删除已有记录。演示账号初始密码可通过 `DEMO_ACCOUNT_PASSWORD`
注入，不应写入版本库。

## 7. 配置文件位置

```text
app/core/config/settings.py
app/core/config/logging_config.py
app/db/session.py
app/db/redis_client.py
app/db/langgraph_checkpoint.py
```

## 8. 安全约定

- API Key、JWT Secret 和数据库生产密码只通过环境变量或密钥服务注入；
- 日志不得输出 API Key、JWT、密码或验证码；
- PostgreSQL 是正式业务数据来源；
- Redis 只保存缓存、锁和临时状态；
- LangGraph Checkpoint 不替代正式业务表；
- 生产环境不得使用示例 JWT Secret 和数据库密码。

## 9. 学生操作指南知识库

操作指南使用 BM25 与 `BAAI/bge-m3` 语义向量混合检索。硅基流动配置可使用：

```dotenv
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
SILICONFLOW_API_KEY=
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DIMENSIONS=1024
```

API Key 只能写入本机 `backend/.env` 或生产密钥服务，不得提交到版本库。

未安装 pgvector 或尚未同步索引时，后端会使用进程内向量索引；完成数据库
备份并确认 PostgreSQL 支持 pgvector 后，可执行：

```powershell
alembic upgrade head
python -m scripts.sync_operation_guides
python -m scripts.verify_operation_guide_search
```

指南内容位于 `app/data/student_operation_guides.py`。修改指南后重新执行同步
命令即可更新数据库向量索引。
