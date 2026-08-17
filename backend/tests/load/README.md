# 学生端 Locust 单次突发测试

## 测试模型

`single_*` 场景采用完全相同的模型：

- `LOAD_TEST_TARGET_USERS` 同时表示虚拟学生数和本轮固定请求数；
- 所有学生以“目标人数/秒”的速度在约 1 秒内启动，不逐级增加；
- 每名学生绑定一个 JWT，只请求目标接口一次；
- 全部请求完成后自动停止，不需要传 `-u`、`-r`、`-t`；
- 命令中的 `--csv` 和 `--html` 决定报告保存位置。

公共参数：

```powershell
cd "D:\物理实验智能排课\backend"
$env:LOCUST_TOKEN_FILE="D:\物理实验智能排课\backend\load-data\students.csv"
$env:LOAD_TEST_TARGET_USERS="200"
$env:LOAD_TEST_REQUIRE_UNIQUE_USERS="true"
```

## 查询接口

命令行只需切换场景名：

```powershell
# 首页/选课卡片
$env:LOCUST_SCENARIO="single_dashboard"

# 忙闲图
$env:LOCUST_SCENARIO="single_bitmap"

# 我的实验课表
$env:LOCUST_SCENARIO="single_timetable"
```

然后运行：

```powershell
.\.venv\Scripts\python.exe -m locust `
  -f tests\load\locustfile.py `
  --host http://127.0.0.1:8001 `
  --headless `
  --csv reports\single-read-200 `
  --html reports\single-read-200.html
```

## 单次选课

影响范围：Redis预检通过的请求会先进入可靠队列，随后由后台Worker真实写入数据库；数据库选课记录、场次人数和相关 Redis 缓存都会按现有业务逻辑发生变化。只能连接隔离测试环境。

测试数据文件必须包含 `session_id` 列。可以让多个学生竞争同一场次，也可以提供多个场次分散请求。

```powershell
$env:LOCUST_SCENARIO="single_select"
$env:LOAD_TEST_TARGET_USERS="200"
$env:LOCUST_TOKEN_FILE="D:\物理实验智能排课\backend\load-data\students.csv"
$env:LOAD_TEST_SESSION_POOL_FILE="D:\物理实验智能排课\backend\load-data\session_pool.csv"
$env:LOAD_TEST_ENV="testing"
$env:LOAD_TEST_ALLOW_WRITES="true"
$env:LOAD_TEST_RUN_ID="single-select-$(Get-Date -Format yyyyMMdd-HHmmss)"

.\.venv\Scripts\python.exe -m locust `
  -f tests\load\locustfile.py `
  --host http://127.0.0.1:8001 `
  --headless `
  --csv reports\single-select-200 `
  --html reports\single-select-200.html
```

默认只测试入口受理能力。报告中的 `processing` 表示Redis预检通过、库存已预留且任务已经可靠写入队列；`full`、`duplicate`、`already_selected`、`busy`、`ineligible` 等表示Redis已经给出最终业务结果。非200、坏JSON、缺少 `request_id` 和未知结果才计为系统失败。

测试5000个学生的一秒突发受理：

```powershell
$env:LOAD_TEST_TARGET_USERS="5000"
$env:LOAD_TEST_VERIFY_ASYNC_RESULTS="false"
```

该模式测量的是“5000个请求是否在约1秒内被Redis受理或明确拒绝”，不会把数据库Worker的排队时间计入选课入口响应时间。但压测结束后仍应检查队列积压和数据库最终一致性。

如果需要在同一轮测试中等待每条任务的最终数据库结果：

```powershell
$env:LOAD_TEST_VERIFY_ASYNC_RESULTS="true"
$env:LOAD_TEST_ASYNC_SELECTION_TIMEOUT_SECONDS="120"
```

开启后会额外统计选课状态查询接口；此时报告同时包含入口响应时间和后台最终完成时间，不适合作为纯入口5000 QPS指标。

## 单次退课

影响范围：每名学生会真实退掉一个已选场次，并触发原有计数、库存和缓存失效逻辑。退课测试通常应在选课测试和数据核对完成后执行。

准备一个不提交到版本库的 CSV，每行必须是有效的一一对应关系：

```csv
token,session_id
学生A的JWT,学生A已经选中的场次UUID
学生B的JWT,学生B已经选中的场次UUID
```

运行：

```powershell
$env:LOCUST_SCENARIO="single_deselect"
$env:LOAD_TEST_TARGET_USERS="200"
$env:LOAD_TEST_DESELECT_TARGET_FILE="D:\安全位置\deselect_targets.csv"
$env:LOAD_TEST_ENV="testing"
$env:LOAD_TEST_ALLOW_WRITES="true"
$env:LOAD_TEST_RUN_ID="single-deselect-$(Get-Date -Format yyyyMMdd-HHmmss)"

.\.venv\Scripts\python.exe -m locust `
  -f tests\load\locustfile.py `
  --host http://127.0.0.1:8001 `
  --headless `
  --csv reports\single-deselect-200 `
  --html reports\single-deselect-200.html
```

如果出现 `not_enrolled`，说明映射文件中的场次已不是该学生的有效选课；这属于测试数据问题，会单独统计。

## 单次 AI 智能咨询

AI 场景不会写选课数据，但会真实调用当前配置的 AI 服务，可能产生模型调用费用，因此也要求显式打开测试开关。

默认使用带权随机问题池，每名学生只抽取并发送一个问题：

- `schedule`（课表、下一节实验）：40%；
- `progress`（已完成、未完成实验）：25%；
- `selection`（可选场次、推荐、不可选原因）：20%；
- `guide`（退课、调课、补做指南）：15%。

报告会按上述类别分行统计请求数和响应时间。

```powershell
$env:LOCUST_SCENARIO="single_ai_consult"
$env:LOAD_TEST_TARGET_USERS="50"
$env:LOCUST_TOKEN_FILE="D:\物理实验智能排课\backend\load-data\students.csv"
$env:LOAD_TEST_ENV="testing"
$env:LOAD_TEST_ALLOW_AI="true"
$env:LOAD_TEST_AI_PROMPT_MODE="random"
Remove-Item Env:LOAD_TEST_AI_PROMPT -ErrorAction SilentlyContinue
$env:LOAD_TEST_AI_TIMEOUT_SECONDS="120"
$env:LOAD_TEST_RUN_ID="single-ai-$(Get-Date -Format yyyyMMdd-HHmmss)"

.\.venv\Scripts\python.exe -m locust `
  -f tests\load\locustfile.py `
  --host http://127.0.0.1:8001 `
  --headless `
  --csv reports\single-ai-50 `
  --html reports\single-ai-50.html
```

AI 达到后端并发上限时返回的 HTTP 429 会以 `[limited-429]` 单独统计，表示并发保护生效，不计作服务崩溃；其他非 200 响应仍计为失败。AI 测试应从较小人数开始，避免一次性产生大量外部模型请求。

如需进行与之前相同的固定问题基准测试，设置：

```powershell
$env:LOAD_TEST_AI_PROMPT_MODE="fixed"
$env:LOAD_TEST_AI_PROMPT="请查询我的实验课表"
```

设置后所有学生都会问这一个问题；把模式改回 `random` 即可恢复随机问题池，遗留的提示词变量不会再意外覆盖随机模式。

也可以通过 `LOAD_TEST_AI_PROMPT_FILE` 指定自定义 CSV。文件格式为：

```csv
category,prompt,weight
schedule,请查询我的实验课表,40
selection,帮我推荐一个不冲突的实验场次,20
guide,如何申请调课,10
```

权重不要求相加等于100，代码会按各行权重的相对比例随机抽取：

```powershell
$env:LOAD_TEST_AI_PROMPT_FILE="D:\安全位置\ai_prompts.csv"
```

## 报告位置

以上示例会在 `D:\物理实验智能排课\backend\reports` 中生成：

- `名称.html`：可直接打开的汇总报告；
- `名称_stats.csv`：请求数、失败数、平均值、P95/P99 等；
- `名称_failures.csv`：失败原因；
- Locust 生成的其他 CSV 明细。

修改 `LOAD_TEST_TARGET_USERS` 后，也应同步修改报告文件名，避免覆盖上一轮结果。
