# 运维部署手册

本文面向单机 Docker Compose 部署。数据库和 Redis 使用命名卷持久化，不需要从开发机
复制数据目录。

## 1. 获取并检查仓库

确认根目录至少包含：

```text
Dockerfile
docker-compose.yml
backend/.env.example
backend/alembic/
frontend/package-lock.json
```

复制环境模板并填写密钥：

```bash
cp backend/.env.example backend/.env
```

生产环境至少更换 `POSTGRES_PASSWORD`、`JWT_SECRET_KEY` 和模型 API Key。

## 2. 验证编排配置

```bash
docker compose config --quiet
```

该命令不启动容器。若提示缺少 `backend/.env`，先完成配置文件复制。

## 3. 首次启动

```bash
docker compose up -d --build
docker compose ps
```

启动顺序为 PostgreSQL、Redis、应用。应用在启动前自动执行：

```bash
alembic upgrade head
```

只应执行迁移升级；首次部署不得执行 `alembic revision --autogenerate`。

## 4. 创建管理员

```bash
docker compose exec app python -m scripts.create_admin --login-name admin
```

脚本不会覆盖已有管理员。如果登录名已被教师或学生账号使用，会退出并报告冲突。

自动化平台可从标准输入提供密码，但不得把密码写进 Git：

```bash
printf '%s\n' "$BOOTSTRAP_ADMIN_PASSWORD" | \
  docker compose exec -T app python -m scripts.create_admin \
  --login-name admin --password-stdin
```

创建完成后应从部署环境移除一次性变量 `BOOTSTRAP_ADMIN_PASSWORD`。

## 5. 可选演示资料

仅在独立演示环境中执行：

```bash
docker compose exec app python -m scripts.bootstrap_demo --confirm-demo-data
```

脚本要求输入演示教师和学生的共用密码，创建的账号为 `demo_teacher` 与
`demo_student`。所有记录均使用 `DEMO-` 标识；脚本会拒绝向已经包含机构或教学资料
的数据库写入，生产部署不得执行。

## 6. RAG 索引

未配置 Embedding 服务时无需同步，系统使用 BM25。配置 Embedding Key 后执行：

```bash
docker compose exec app python -m scripts.sync_operation_guides
docker compose exec app python -m scripts.verify_operation_guide_search
```

同步过程会调用外部 Embedding 服务，可能产生网络请求和供应方费用。

## 7. 验收

```bash
bash scripts/verify-deployment.sh
```

还应人工完成：管理员登录、基础资料查询，以及当前部署范围内的一条核心业务流程。

## 8. 查看日志与重启

```bash
docker compose logs --tail 200 app
docker compose restart app
docker compose ps
```

日志中不得出现 API Key、JWT、数据库密码或验证码。

## 9. 升级

升级前先完成 PostgreSQL 备份，然后获取经过审核的新版本代码并执行：

```bash
docker compose build app
docker compose up -d
docker compose ps
```

应用启动时执行迁移。若新版本迁移不可逆，必须在发布说明中明确回滚依赖数据库恢复。

## 10. 停止

保留数据卷停止：

```bash
docker compose down
```

不要在普通停止或升级时添加 `-v`。`docker compose down -v` 会删除 PostgreSQL 和
Redis 数据卷，造成数据丢失，只能在确认备份有效且明确需要重建环境时使用。
