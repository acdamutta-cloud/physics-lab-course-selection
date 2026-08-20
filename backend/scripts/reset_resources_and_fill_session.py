"""一次性数据操作：清理资源异常 + 恢复全部设备 + 张伟振动场次选满 24 人。

默认 dry-run 打印影响清单；实执行需同时传 --execute 与 --confirm RESET-FILL-24。

清理部分：
- 删除全部 resource_issue_report（级联清除 relocation plan/item、issue_asset、
  observation；asset_event 与 inventory_movement 的 resource_issue_id 置 NULL）
- 全部非 SCRAPPED 且非 AVAILABLE 的 equipment_asset 恢复 AVAILABLE
- 对全部 lab_equipment_inventory 重算（sync_inventory_counts）
- 清理 Redis resource-impact:* 缓存

选满部分：
- 目标场次 deed1527-42ee-4366-b172-b6b91a5249d5（AI-V101-00737, capacity 24）
- 插入 24 条 StudentProjectRecord（真实学生，排除已有活跃选课记录者）
- 场次 selected_count=24、status=FULL；Redis session:stock:<id>=0
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text

from app.db.redis_client import get_redis_client
from app.db.session import AsyncSessionFactory
from app.models.enrollment import StudentProjectRecord
from app.models.scheduling import ExperimentSession
from app.services.equipment_asset_service import sync_inventory_counts
from app.services.selection_service import _stock_key

CONFIRMATION_TOKEN = "RESET-FILL-24"

TARGET_SESSION_ID = UUID("deed1527-42ee-4366-b172-b6b91a5249d5")
TERM_ID = UUID("0a975078-8fe1-4ef0-a785-74b04eab5dca")
COURSE_ID = UUID("22f768ce-81cb-54db-aeea-9d8f47cd8e9f")
PROJECT_ID = UUID("955354f6-f0d3-5727-a2d7-a998b2cee03f")
FILL_COUNT = 24


async def _print_cleanup_preview(session) -> dict[str, object]:
    issue_rows = list(
        (
            await session.execute(
                text(
                    "SELECT report_no, status, issue_type FROM resource_issue_report "
                    "ORDER BY created_at"
                )
            )
        ).all()
    )
    asset_rows = list(
        (
            await session.execute(
                text(
                    "SELECT instrument_no, status FROM equipment_asset "
                    "WHERE status <> 'AVAILABLE' AND status <> 'SCRAPPED' "
                    "ORDER BY instrument_no"
                )
            )
        ).all()
    )
    inventory_count = int(
        await session.scalar(text("SELECT COUNT(*) FROM lab_equipment_inventory")) or 0
    )
    print("== 清理预览 ==")
    print(f"issue 记录 {len(issue_rows)} 条:")
    for row in issue_rows:
        print(f"  {row.report_no} [{row.status}/{row.issue_type}]")
    print(f"非正常设备 {len(asset_rows)} 台:")
    for row in asset_rows:
        print(f"  {row.instrument_no} [{row.status}]")
    print(f"待重算库存 {inventory_count} 条")
    return {"issues": len(issue_rows), "assets": len(asset_rows)}


async def _cleanup(session) -> None:
    # 恢复所有非报废设备到 AVAILABLE（含 UNDER_REPAIR / DISABLED）
    await session.execute(
        text(
            "UPDATE equipment_asset SET status = 'AVAILABLE', updated_at = now() "
            "WHERE status <> 'AVAILABLE' AND status <> 'SCRAPPED'"
        )
    )
    # 删除带 issue 关联的设备事件，避免残留"检修中"轨迹
    await session.execute(
        text("DELETE FROM equipment_asset_event WHERE resource_issue_id IS NOT NULL")
    )
    await session.execute(text("DELETE FROM equipment_inventory_movement"))
    # 删除全部异常记录（级联 relocation plan/item、issue_asset、observation）
    await session.execute(text("DELETE FROM resource_issue_report"))

    inventory_ids = list(
        (
            await session.execute(
                text("SELECT id FROM lab_equipment_inventory ORDER BY id")
            )
        ).scalars()
    )
    for inventory_id in inventory_ids:
        await sync_inventory_counts(session, inventory_id)

    # 清理 resource-impact 缓存
    redis = get_redis_client()
    async for key in redis.scan_iter("resource-impact:*"):
        await redis.delete(key)


async def _print_fill_preview(session) -> list[tuple[str, str]]:
    session_row = await session.execute(
        text(
            "SELECT session_code, capacity, selected_count, status "
            "FROM experiment_session WHERE id = :sid"
        ),
        {"sid": TARGET_SESSION_ID},
    )
    print("== 选满预览 ==")
    for row in session_row.mappings():
        print("目标场次:", dict(row))

    candidates = list(
        (
            await session.execute(
                text(
                    "SELECT student_no, name FROM student "
                    "WHERE academic_status = 'ACTIVE' "
                    "  AND id NOT IN ("
                    "    SELECT student_id FROM student_project_record "
                    "    WHERE status IN ('SELECTED', 'COMPLETED', 'ABSENT', "
                    "'MAKEUP_PENDING')"
                    "  ) "
                    "ORDER BY student_no LIMIT :limit"
                ),
                {"limit": FILL_COUNT},
            )
        ).all()
    )
    total = int(
        await session.scalar(
            text(
                "SELECT COUNT(*) FROM student WHERE academic_status = 'ACTIVE' "
                "  AND id NOT IN ("
                "    SELECT student_id FROM student_project_record "
                "    WHERE status IN ('SELECTED', 'COMPLETED', 'ABSENT', "
                "'MAKEUP_PENDING')"
                "  )"
            )
        )
        or 0
    )
    print(f"候选学生（排除已有活跃选课记录）: {total} 人，将选 {len(candidates)} 人:")
    for no, name in candidates:
        print(f"  {no} {name}")
    return [(row[0], row[1]) for row in candidates]


async def _fill(session, students: list[tuple[str, str]]) -> None:
    student_ids = list(
        (
            await session.execute(
                text("SELECT id FROM student WHERE student_no = ANY(:nos)"),
                {"nos": [no for no, _ in students]},
            )
        ).scalars()
    )
    now = datetime.now(timezone.utc)
    for student_id in student_ids:
        session.add(
            StudentProjectRecord(
                student_id=student_id,
                term_id=TERM_ID,
                course_id=COURSE_ID,
                project_id=PROJECT_ID,
                session_id=TARGET_SESSION_ID,
                requirement_type="OPTIONAL",
                status="SELECTED",
                selected_at=now,
                report_status="NOT_REQUIRED",
                version_no=1,
            )
        )
    session_row = await session.execute(
        text("SELECT capacity FROM experiment_session WHERE id = :sid"),
        {"sid": TARGET_SESSION_ID},
    )
    capacity = session_row.scalar_one()
    await session.execute(
        text(
            "UPDATE experiment_session SET selected_count = :n, status = 'FULL', "
            "updated_at = now() WHERE id = :sid"
        ),
        {"n": len(student_ids), "sid": TARGET_SESSION_ID},
    )
    await session.flush()
    await session.commit()
    # 库存键同步为 0
    await get_redis_client().set(_stock_key(TARGET_SESSION_ID), max(0, capacity - len(student_ids)))
    print(f"已插入 {len(student_ids)} 条选课记录，场次置 FULL，库存键={0}")


async def _verify(session) -> None:
    print("== 验证 ==")
    issue_left = int(
        await session.scalar(text("SELECT COUNT(*) FROM resource_issue_report")) or 0
    )
    print("异常记录剩余:", issue_left)
    abnormal = list(
        (
            await session.execute(
                text(
                    "SELECT instrument_no, status FROM equipment_asset "
                    "WHERE status <> 'AVAILABLE' ORDER BY instrument_no"
                )
            )
        ).all()
    )
    print("非 AVAILABLE 设备:", len(abnormal), [f"{r[0]}[{r[1]}]" for r in abnormal])
    session_row = await session.execute(
        text(
            "SELECT session_code, capacity, selected_count, status "
            "FROM experiment_session WHERE id = :sid"
        ),
        {"sid": TARGET_SESSION_ID},
    )
    for row in session_row.mappings():
        print("目标场次:", dict(row))
    records = list(
        (
            await session.execute(
                text(
                    "SELECT s.student_no, s.name, r.status, r.requirement_type "
                    "FROM student_project_record r JOIN student s ON s.id = r.student_id "
                    "WHERE r.session_id = :sid ORDER BY s.student_no"
                ),
                {"sid": TARGET_SESSION_ID},
            )
        ).all()
    )
    print(f"场次选课记录 {len(records)} 条:")
    for row in records:
        print(f"  {row[0]} {row[1]} [{row[2]}/{row[3]}]")


async def main(execute: bool, confirmation: str) -> None:
    async with AsyncSessionFactory() as session:
        preview = await _print_cleanup_preview(session)
        students = await _print_fill_preview(session)
        if not execute:
            print("dry-run 结束；如需实执行：--execute --confirm RESET-FILL-24")
            return
        if confirmation != CONFIRMATION_TOKEN:
            raise SystemExit(f"Refusing destructive execution: pass --confirm {CONFIRMATION_TOKEN}")
        if len(students) < FILL_COUNT:
            raise SystemExit(f"候选学生不足：{len(students)} < {FILL_COUNT}")
        await _cleanup(session)
        await _fill(session, students)
        await _verify(session)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    asyncio.run(main(execute=args.execute, confirmation=args.confirm))
