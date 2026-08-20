# 参与开发

## 开始之前

- 不提交 `.env`、API Key、数据库备份、运行日志或真实师生数据；
- 业务规则修改应说明适用场景和迁移影响；
- 数据库结构修改必须通过新的 Alembic 迁移完成；
- RAG 知识内容必须经过版权和隐私检查；
- 不在迁移中加入生产机构的真实基础资料。

## 本地检查

后端：

```powershell
cd backend
python -m pip install -r requirements-dev.txt
python -m pytest tests/unit -q
```

前端：

```powershell
cd frontend
npm ci
npm run build
```

部署配置：

```powershell
Copy-Item backend/.env.example backend/.env
docker compose config --quiet
```

不要提交为了本地检查创建的 `backend/.env`。

## 提交内容

变更说明应包含：目的、影响范围、验证方式、配置变化和数据库迁移情况。修复缺陷时应
尽量增加回归测试。文档改写应保留原意，不添加未经验证的性能、容量或兼容性结论。
