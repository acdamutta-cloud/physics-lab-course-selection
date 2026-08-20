"""临时审计：找出业务代码中零引用的数据库字段。"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import Base

# 收集所有表 + 模型类名
table_model = {}
for cls in Base.__subclasses__():
    if hasattr(cls, "__tablename__"):
        table_model[cls.__tablename__] = cls.__name__

# 收集代码文件（排除 models 目录）
code_files: list[str] = []
for root, _dirs, files in os.walk("app"):
    if "models" in root.split(os.sep):
        continue
    for f in files:
        if f.endswith(".py"):
            code_files.append(os.path.join(root, f))

corpus = ""
for p in code_files:
    with open(p, encoding="utf-8", errors="ignore") as fh:
        corpus += fh.read()

zero_ref: list[tuple[str, str, str]] = []
low_ref: list[tuple[str, str, str, int]] = []

for table in Base.metadata.sorted_tables:
    model_name = table_model.get(table.name, table.name)
    for col in table.columns:
        col_name = col.name
        # 裸词出现次数（任何形式：属性访问、字符串、dict 键）
        total = len(re.findall(rf"\b{re.escape(col_name)}\b", corpus))
        if total == 0:
            zero_ref.append((table.name, model_name, col_name))
        elif total <= 2:
            low_ref.append((table.name, model_name, col_name, total))

print("=== 零引用字段 ===")
for t, m, c in sorted(zero_ref):
    print(f"{t} ({m}).{c}")
print(f"\n=== 低引用字段 (1-2次) ===")
for t, m, c, n in sorted(low_ref):
    print(f"{t} ({m}).{c}  x{n}")
print(f"\nzero={len(zero_ref)} low={len(low_ref)}")
