# 故障排查

## Compose 提示项目名为空

根 Compose 已固定 `name: physics-lab`。如果仍出现该错误，确认使用的是仓库根目录最新
的 `docker-compose.yml`，或临时执行：

```bash
docker compose -p physics-lab config --quiet
```

## 缺少 backend/.env

```bash
cp backend/.env.example backend/.env
```

随后填写数据库密码、JWT 密钥和所选模型 API Key。

## 应用容器无法启动

```bash
docker compose ps
docker compose logs --tail 200 postgres
docker compose logs --tail 200 redis
docker compose logs --tail 200 app
```

优先检查 PostgreSQL 密码是否为空、数据库服务是否健康，以及 Alembic 迁移错误。

## AI 对话提示供应方不可用

确认 `MODEL_PROVIDER` 与对应 Key 匹配。当前默认配置为：

```dotenv
MODEL_PROVIDER=dashscope
DASHSCOPE_MODEL=qwen3-14b
```

修改 `.env` 后重建应用容器环境：

```bash
docker compose up -d --force-recreate app
```

## RAG 只有 BM25

检查 Embedding Key、模型和维度，然后重新执行知识同步。外部 Embedding 调用失败时，
服务会降级为 BM25，这是预期的可用性保护。

## 前端页面不可访问

确认镜像构建阶段 `npm run build` 成功，并检查 `app` 容器健康状态。生产前端由后端在
同一个 `8000` 端口托管，不需要单独启动 Vite 开发服务器。
