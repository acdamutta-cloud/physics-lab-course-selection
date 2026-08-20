# RAG 知识库与复现

## 当前知识源

学生端系统操作指南位于：

```text
backend/app/data/student_operation_guides.py
```

该文件是知识内容的事实来源，可以提交 GitHub。知识内容更新时应保持 `guide_id`
稳定，并同步修改标题、主题、关键词、步骤、注意事项和适用系统版本。

## 检索流程

```text
用户问题
  ├─ BM25 关键词检索
  └─ BAAI/bge-m3 向量检索（配置 Embedding 时）
        ↓
      结果融合
        ↓
Qwen3-14B 根据检索结果组织回答
```

Embedding 未启用或向量索引不可用时，服务降级为 BM25，不影响知识源读取。

## 可提交内容

- 原始、已审阅且无隐私的知识文档；
- 知识元数据和内容版本；
- 分块、检索、同步和验证代码；
- 已脱敏的 RAG 评测集；
- pgvector 表结构迁移。

## 不提交内容

- API Key 和真实 `.env`；
- PostgreSQL/pgvector 数据目录或数据库备份；
- 已生成的 embedding 和 HNSW 索引；
- 真实用户对话、日志和未脱敏评测报告；
- 无公开授权的第三方文档。

## 目标环境重建

Alembic 创建 `operation_guide_index` 表与索引。配置 Embedding 服务后执行：

```bash
python -m scripts.sync_operation_guides
python -m scripts.verify_operation_guide_search
```

同步脚本按照知识内容哈希幂等更新索引。更换 Embedding 模型、向量维度或知识内容后
需要重新执行同步。生产运维应记录同步使用的模型、维度和知识版本。

## 评测数据

`backend/evals/datasets/` 中的 JSONL 用于检索、路由和工具调用评测，不是生产知识库。
新增案例前必须经过匿名化检查，不能放入真实学号、手机号、账号、Token 或内部地址。
