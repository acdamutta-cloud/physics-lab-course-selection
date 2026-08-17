# 学生端 AI 智能咨询评测

此目录与生产业务代码、普通单元测试隔离，用于在 LangSmith 上评测 Qwen3-14B 的学生咨询效果。

## 目录内容

- `datasets/`：300条分层评测数据，包含RAG、上下文、工具、路由安全和鲁棒性配对。
- `fixtures/`：不含真实身份信息的固定学生课表上下文。
- `evaluators/`：规划、工具、RAG、事实、安全和DeepSeek裁判评分器。
- `run_evaluation.py`：数据集同步、实验执行和本地报告入口。
- `reports/`：运行后生成的本地汇总，不应提交包含真实trace的报告。

## 必要配置

在 `backend/.env` 中配置：

```env
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=你的LangSmith密钥
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_PROJECT=physics-lab-student-consultation

EVAL_JUDGE_PROVIDER=deepseek
EVAL_MAX_CONCURRENCY=4
EVAL_REPETITIONS=1
EVAL_STUDENT_NO=D2024010001
```

被测模型沿用业务环境中的 `MODEL_PROVIDER` 配置；当前可使用阿里云百炼
`MODEL_PROVIDER=dashscope`。DeepSeek裁判复用现有 `DEEPSEEK_API_KEY`、
`DEEPSEEK_BASE_URL` 和 `DEEPSEEK_MODEL`，与被测模型相互独立。

## 安装与运行

```powershell
cd backend
.\.venv\Scripts\pip.exe install -r requirements-eval.txt

# 不联网，仅校验300条数据结构
.\.venv\Scripts\python.exe evals\run_evaluation.py --suite full --validate-only

# 约30条冒烟评测
.\.venv\Scripts\python.exe evals\run_evaluation.py --suite smoke

# 完整评测
.\.venv\Scripts\python.exe evals\run_evaluation.py --suite full

# 完全本地评测：不上传LangSmith，仍调用Qwen和DeepSeek
.\.venv\Scripts\python.exe evals\run_evaluation.py --suite full

# 先用5条验证本地链路
.\.venv\Scripts\python.exe evals\run_evaluation.py --suite smoke --limit 5

# 只将完整300条数据同步到LangSmith，不调用模型
.\.venv\Scripts\python.exe evals\run_evaluation.py --suite full --sync-only

# 只运行RAG且暂不调用DeepSeek裁判
.\.venv\Scripts\python.exe evals\run_evaluation.py --suite rag --no-judge
```

运行结束后，终端会输出 LangSmith 实验链接，并在 `evals/reports/` 生成Markdown、JSON和CSV报告。

## 工具数据库 fixture

退选、调课、换组和补做会读取评测学生的测试数据库状态。相关用例通过
`inputs.database_fixture_id` 显式选择 `selected_standard` 或 `no_selections`。
运行时会在同一数据库事务中临时准备固定记录，并将当前教学周固定为第5周，
从而稳定区分未开始的调课来源和已开始的补做来源。每条用例结束后统一回滚，
不会改变评测学生原有记录。

`tool_result_accuracy` 会同时检查工具结果状态、匹配数量、项目名称、申请类型和是否需要确认，例如：

- 已选交流电桥：`preview_deselection = MATCH, count = 1`；
- 未选任何实验：`preview_deselection = NO_MATCH, count = 0`；
- 唯一可调课来源：`prepare_adjustment_entry = UNIQUE, count = 1`。

数据库 fixture 用例在进程内串行执行，避免并发事务对同一评测学生造成锁竞争；
其他不依赖 fixture 的评测仍可按 `EVAL_MAX_CONCURRENCY` 并发运行。

也可以只对当前一次运行临时覆盖，例如：

```powershell
.\.venv\Scripts\python.exe evals\run_evaluation.py --suite tool --max-concurrency 6
```

默认并发为4。若模型平台出现429或连接超时，请降为2；不建议在未确认供应商限流额度时继续提高。

## 评分口径

- 代码指标通常为0—1分；没有对应参考条件时返回“不适用”，不再按1分计入均值。
- `intent_accuracy` 接受明确登记的等价意图，`preferred_intent_accuracy` 另行显示首选意图是否命中。
- `rag_top1_accuracy`、`rag_recall_at_2` 和 `rag_mrr` 根据知识库检索到的 `guide_id` 计算。
- 上下文事实只依据 `student_context.json` 中固定课表，不要求调用工具。
- 工具参数本身为空是当前安全设计：实体、偏好和申请类型位于结构化计划中，因此分别由
  `entity_f1`、`preference_f1`、`intent_accuracy` 和 `tool_result_accuracy` 评价。
- DeepSeek裁判的六项指标直接使用0—10分，满分10分；它会同时看到标准要点、
  实际检索/工具结果及脱敏后的评测上下文。同义表达不要求逐字匹配。

每次实验结束后，`reports/review-cases.csv` 会把原问题、参考要点、模型答案、
工具调用和各项得分放在同一行，便于人工复核。`reports/review-cases.html`
提供搜索、分类和失败筛选；`reports/local-progress.jsonl` 每完成一条就落盘，
即使中途停止也能保留已经完成的原始结果。

本地模式现在是默认行为，会强制关闭 LangSmith/LangChain Trace 上传，不要求
`LANGSMITH_API_KEY`，也不消耗 LangSmith Trace 额度；但被测 Qwen、DeepSeek
仍会按评测配置正常调用。只有显式增加 `--langsmith`，或使用 `--sync-only`，
才会连接 LangSmith。`--local-only` 继续保留为兼容参数。
裁判和RAG向量服务仍使用各自API。

## 安全边界

- 评测图只包含咨询系统现有的只读查询、退选预览和申请入口准备工具。
- 数据库会话在每条样本结束后回滚，不执行确认选课、真实退选或提交申请。
- LangSmith输入使用fixture标识，不上传本地配置的学生学号。
- Trace通过anonymizer清理学号、UUID、手机号和疑似密钥。
