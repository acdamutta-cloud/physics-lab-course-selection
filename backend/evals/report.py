from __future__ import annotations

import csv
import html
import json
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from evals.config import QUALITY_THRESHOLDS, REPORT_DIR


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "dict"):
        return value.dict()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return {"value": str(value)}


def _feedback_items(row: dict[str, Any]) -> list[dict[str, Any]]:
    evaluation = row.get("evaluation_results") or row.get("feedback") or {}
    if isinstance(evaluation, dict):
        values = evaluation.get("results") or evaluation.get("feedback") or []
    else:
        values = evaluation
    return [_as_dict(item) for item in values] if isinstance(values, list) else []


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _html_report(review_rows: list[dict[str, str]], experiment_name: str) -> str:
    cards: list[str] = []
    for index, row in enumerate(review_rows, start=1):
        scores = row.get("scores", "{}")
        has_error = bool(row.get("error"))
        low_score = has_error or any(
            f'"{key}": 0' in scores
            for key in ("intent_accuracy", "tool_selection_f1", "safety_gate")
        )
        status = "failed" if low_score else "passed"
        cards.append(
            f'''<article class="case {status}" data-category="{html.escape(row['category'])}" data-status="{status}">
<header><span>#{index} · {html.escape(row['case_id'])}</span><span>{html.escape(row['category'])} · {status}</span></header>
<h2>{html.escape(row['question'])}</h2>
<div class="grid"><section><h3>参考标准</h3>
<p><b>意图：</b>{html.escape(row['expected_intent'])}</p>
<p><b>回答要点：</b>{html.escape(row['expected_answer_points'])}</p>
<p><b>事实：</b>{html.escape(row['expected_facts'])}</p>
<p><b>工具：</b>{html.escape(row['expected_tools'])}</p>
<p><b>工具结果：</b>{html.escape(row.get('expected_tool_results', ''))}</p></section>
<section><h3>模型结果</h3>
<p><b>意图：</b>{html.escape(row['actual_intent'])}</p>
<div class="answer">{html.escape(row['model_answer'])}</div>
<details><summary>工具调用与结果</summary><pre>{html.escape(row['actual_tools'])}\n{html.escape(row.get('actual_tool_results', ''))}</pre></details></section></div>
<details><summary>全部评分</summary><pre>{html.escape(scores)}</pre></details>
<details><summary>评分说明</summary><pre>{html.escape(row.get('feedback_details', ''))}</pre></details>
{f'<p class="error">{html.escape(row["error"])}</p>' if has_error else ''}</article>'''
        )
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI智能咨询逐条评测</title><style>
body{{font-family:system-ui,"Microsoft YaHei",sans-serif;margin:0;background:#f4f7fa;color:#163047}}main{{max-width:1280px;margin:auto;padding:24px}}
.toolbar{{position:sticky;top:0;background:#f4f7fa;padding:12px 0;z-index:2}}select,input{{padding:9px;margin-right:8px;border:1px solid #b8c8d5;border-radius:8px}}
.case{{background:white;margin:16px 0;padding:18px;border-radius:12px;border-left:5px solid #2b908f;box-shadow:0 4px 16px #17324d12}}.case.failed{{border-left-color:#c94d58}}
header{{display:flex;justify-content:space-between;color:#5d7183}}h2{{font-size:18px}}h3{{font-size:15px}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}
section{{background:#f7fafc;padding:14px;border-radius:9px}}.answer{{white-space:pre-wrap;line-height:1.7}}pre{{white-space:pre-wrap;word-break:break-word}}.error{{color:#b32836}}
@media(max-width:760px){{.grid{{grid-template-columns:1fr}}}}</style></head><body><main>
<h1>AI智能咨询逐条评测</h1><p>{html.escape(experiment_name)} · 共 {len(review_rows)} 条</p>
<div class="toolbar"><input id="search" placeholder="搜索问题或用例编号"><select id="category"><option value="">全部分类</option><option>RAG</option><option>CONTEXT</option><option>TOOL</option><option>ROUTING_SAFETY</option></select><select id="status"><option value="">全部结果</option><option value="failed">只看失败</option><option value="passed">只看其他</option></select></div>
{''.join(cards)}</main><script>
const q=document.querySelector('#search'),c=document.querySelector('#category'),s=document.querySelector('#status');function f(){{document.querySelectorAll('.case').forEach(x=>{{x.hidden=!(x.innerText.toLowerCase().includes(q.value.toLowerCase())&&(!c.value||x.dataset.category===c.value)&&(!s.value||x.dataset.status===s.value))}})}}[q,c,s].forEach(x=>x.addEventListener('input',f));</script></body></html>'''


def write_local_reports(
    results: Iterable[Any], *, experiment_name: str
) -> dict[str, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    raw_rows = list(results)
    rows = [_as_dict(item) for item in raw_rows]
    metric_values: dict[str, list[float]] = defaultdict(list)
    failed: list[dict[str, Any]] = []
    for row in rows:
        failures: list[str] = []
        for feedback in _feedback_items(row):
            key = str(feedback.get("key") or "unknown")
            score = feedback.get("score")
            if isinstance(score, (int, float)):
                metric_values[key].append(float(score))
                full_score = 10.0 if key.startswith("judge_") else 1.0
                if float(score) < full_score:
                    failures.append(f"{key}={float(score):.3f}")
        if failures:
            example = row.get("example") or {}
            metadata = example.get("metadata", {}) if isinstance(example, dict) else {}
            failed.append(
                {
                    "example_id": row.get("reference_example_id")
                    or row.get("example_id")
                    or metadata.get("case_id")
                    or "",
                    "failures": "; ".join(failures),
                    "error": row.get("error") or "",
                }
            )
    averages = {
        key: sum(values) / len(values)
        for key, values in sorted(metric_values.items())
        if values
    }
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    summary_lines = [
        "# AI智能咨询评测报告",
        "",
        f"- 实验：`{experiment_name}`",
        f"- 生成时间：{timestamp}",
        f"- 样本结果：{len(rows)}",
        f"- 存在扣分的样本：{len(failed)}",
        "",
        "## 指标均值",
        "",
    ]
    summary_lines.extend(
        f"- `{key}`：{value:.4f}{' / 10' if key.startswith('judge_') else ''}"
        for key, value in averages.items()
    )
    summary_lines.extend(["", "## 发布门槛", ""])
    for key, threshold in QUALITY_THRESHOLDS.items():
        actual = averages.get(key)
        if actual is None:
            status = "未采集"
        else:
            status = "通过" if actual >= threshold else "未通过"
        scale = " / 10" if key.startswith("judge_") else ""
        actual_text = "-" if actual is None else f"{actual:.4f}{scale}"
        summary_lines.append(
            f"- `{key}`：{status}（实际 {actual_text} / 要求 {threshold:.2f}）"
        )
    summary_path = REPORT_DIR / "latest-summary.md"
    result_path = REPORT_DIR / "latest-results.json"
    failed_path = REPORT_DIR / "failed-cases.csv"
    metrics_path = REPORT_DIR / "category-metrics.csv"
    review_path = REPORT_DIR / "review-cases.csv"
    html_path = REPORT_DIR / "review-cases.html"
    summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    result_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    with failed_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["example_id", "failures", "error"])
        writer.writeheader()
        writer.writerows(failed)
    with metrics_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["metric", "average", "count"])
        writer.writeheader()
        writer.writerows(
            {
                "metric": key,
                "average": f"{averages[key]:.6f}",
                "count": len(values),
            }
            for key, values in sorted(metric_values.items())
        )
    review_fields = [
        "case_id",
        "category",
        "question",
        "expected_intent",
        "acceptable_intents",
        "expected_answer_points",
        "expected_facts",
        "expected_tools",
        "expected_tool_results",
        "actual_intent",
        "model_answer",
        "actual_tools",
        "actual_tool_results",
        "scores",
        "feedback_details",
        "error",
    ]
    review_rows: list[dict[str, str]] = []
    with review_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=review_fields)
        writer.writeheader()
        for raw_row, row in zip(raw_rows, rows, strict=True):
            example = _field(raw_row, "example", None) or row.get("example") or {}
            run = _field(raw_row, "run", None) or row.get("run") or {}
            inputs = _field(example, "inputs", {}) or {}
            reference = _field(example, "outputs", {}) or {}
            metadata = _field(example, "metadata", {}) or {}
            actual = _field(run, "outputs", {}) or {}
            messages = inputs.get("messages") or []
            feedback_scores = {
                str(item.get("key")): item.get("score")
                for item in _feedback_items(row)
            }
            feedback_details = [
                {
                    "key": item.get("key"),
                    "score": item.get("score"),
                    "comment": item.get("comment", ""),
                }
                for item in _feedback_items(row)
            ]
            review_row = {
                    "case_id": metadata.get("case_id", ""),
                    "category": metadata.get("category", ""),
                    "question": messages[-1].get("content", "") if messages else "",
                    "expected_intent": reference.get("expected_intent", ""),
                    "acceptable_intents": _json_text(reference.get("acceptable_intents", [])),
                    "expected_answer_points": _json_text(reference.get("expected_answer_points", [])),
                    "expected_facts": _json_text(reference.get("expected_facts", [])),
                    "expected_tools": _json_text(reference.get("expected_tools", [])),
                    "expected_tool_results": _json_text(reference.get("expected_tool_results", {})),
                    "actual_intent": actual.get("intent", ""),
                    "model_answer": actual.get("answer", ""),
                    "actual_tools": _json_text(actual.get("tool_requests", [])),
                    "actual_tool_results": _json_text(actual.get("tool_results", [])),
                    "scores": _json_text(feedback_scores),
                    "feedback_details": _json_text(feedback_details),
                    "error": _field(run, "error", "") or row.get("error") or "",
                }
            writer.writerow(review_row)
            review_rows.append({key: str(value) for key, value in review_row.items()})
    html_path.write_text(_html_report(review_rows, experiment_name), encoding="utf-8")
    return {
        "summary": summary_path,
        "results": result_path,
        "failed": failed_path,
        "metrics": metrics_path,
        "review": review_path,
        "html": html_path,
    }
