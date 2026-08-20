# 备份与恢复

PostgreSQL 是正式业务数据来源，必须纳入备份。Redis 保存缓存和临时状态，通常通过
应用重新构建，不作为正式业务恢复的唯一依据。

## PostgreSQL 备份

在仓库根目录执行，输出文件应保存到受控备份目录，不要提交 Git：

```bash
docker compose exec -T postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > physics-lab.dump
```

备份文件可能包含账号、师生和教学数据，应加密保存并限制访问。

## 恢复前检查

恢复可能覆盖或冲突目标数据库中的现有数据。操作前必须：

1. 确认目标环境和数据库名称；
2. 备份目标数据库当前状态；
3. 确认应用版本与备份中的迁移版本兼容；
4. 停止应用写入；
5. 在非生产环境演练恢复与登录验证。

具体恢复参数取决于目标库是否为空、是否需要清理现有对象以及权限模型，因此项目不
提供会自动删除现有数据的一键恢复命令。运维应根据恢复方案使用 `pg_restore`，并在
恢复后执行 `alembic current`、健康检查和核心业务验收。

## Redis 恢复

Redis 丢失后先确认 PostgreSQL 正常，再重启 Redis 和应用，使缓存、库存镜像和学生
视图重新预热。不要通过 GitHub 分发 Redis RDB/AOF 文件。

## 数据卷删除警告

`docker compose down -v` 会删除 Compose 管理的 PostgreSQL 与 Redis 数据卷。这是
不可通过重新拉取代码恢复的操作，执行前必须确认备份已经验证可用。
