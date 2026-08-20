#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(dirname -- "$SCRIPT_DIR")
BASE_URL=${BASE_URL:-http://127.0.0.1:8000}

cd "$PROJECT_ROOT"

echo "[1/5] 检查 Compose 配置"
docker compose config --quiet

echo "[2/5] 检查容器状态"
docker compose ps

echo "[3/5] 检查 PostgreSQL 和迁移版本"
docker compose exec -T postgres sh -c \
  'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
docker compose exec -T app alembic current

echo "[4/5] 检查 Redis"
test "$(docker compose exec -T redis redis-cli ping | tr -d '\r')" = "PONG"

echo "[5/5] 检查 HTTP 健康端点"
curl --fail --silent --show-error "$BASE_URL/api/health" >/dev/null

echo "部署基础验收通过。请继续人工验证管理员登录和核心业务流程。"
