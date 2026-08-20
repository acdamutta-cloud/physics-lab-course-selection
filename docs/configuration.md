# 配置说明

应用从 `backend/.env` 读取配置。生产环境也可以由容器平台或密钥服务注入同名变量。
真实配置文件不得提交到 Git。

## 必填配置

| 变量 | 说明 |
| --- | --- |
| `POSTGRES_PASSWORD` | PostgreSQL 密码，不能为空 |
| `JWT_SECRET_KEY` | JWT 签名密钥，生产环境必须使用强随机值 |
| `MODEL_PROVIDER` | `dashscope`、`huggingface` 或 `deepseek` |
| 对应供应方 API Key | 例如 `DASHSCOPE_API_KEY` |

## 默认 AI 对话配置

```dotenv
MODEL_PROVIDER=dashscope
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_MODEL=qwen3-14b
DASHSCOPE_ENABLE_THINKING=false
```

供应方和模型名均可通过环境变量调整。根 Compose 不覆盖这些值。

## RAG Embedding

未配置 Embedding Key 时，操作指南使用 BM25。启用 OpenAI 兼容 Embedding：

```dotenv
EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_BASE_URL=<服务地址>
EMBEDDING_API_KEY=<密钥>
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DIMENSIONS=1024
```

也可使用环境模板中保留的 SiliconFlow 兼容配置。修改模型或向量维度后必须重新同步
RAG 索引，并确认数据库列维度与配置一致。

## 容器内固定地址

生产 Compose 会把以下地址覆盖为容器服务名：

```dotenv
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/1
CELERY_RESULT_BACKEND=redis://redis:6379/2
```

不要把开发机 IP 写入公开部署配置。

## 构建镜像源

Dockerfile 默认使用 Docker Hub、npmjs.org 和 pypi.org。需要内部或国内镜像时，可在
执行 Compose 前设置 `NODE_IMAGE`、`PYTHON_IMAGE`、`NPM_REGISTRY` 与
`PIP_INDEX_URL`。这些变量只改变镜像或依赖下载来源，不改变运行时功能。
