# 物理实验智能排课系统后端

当前目录为 FastAPI + PostgreSQL + Redis + LangGraph + OR-Tools 的后端框架。
现阶段只完成项目结构和基础环境配置，尚未实现具体业务接口。

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

编辑 `.env` 并注入自己的 DeepSeek API Key：

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
```

## 3. DeepSeek 配置说明

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

## 5. 启动 PostgreSQL 和 Redis

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

## 6. 配置文件位置

```text
app/core/config/settings.py
app/core/config/logging_config.py
app/db/session.py
app/db/redis_client.py
app/db/langgraph_checkpoint.py
```

## 7. 安全约定

- API Key、JWT Secret 和数据库生产密码只通过环境变量或密钥服务注入；
- 日志不得输出 API Key、JWT、密码或验证码；
- PostgreSQL 是正式业务数据来源；
- Redis 只保存缓存、锁和临时状态；
- LangGraph Checkpoint 不替代正式业务表；
- 生产环境不得使用示例 JWT Secret 和数据库密码。
