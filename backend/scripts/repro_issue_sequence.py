"""Reproduce the "23 assets, 4 reports" approval sequence.

Scenario: 23 HALL assets, 4 sequential EQUIPMENT_FAILURE reports.
Expectation (user requirement): first 3 approvals pass directly
(PROCESSING), the 4th approval must go RELOCATION_REQUIRED (capacity
19 < requirement 20 after deducting the 4th asset).

Tests two session modes to reproduce the earlier anomaly where the 4th
approval stayed PROCESSING and the inventory row only counted 3 deductions.
"""
import asyncio
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text

from app.db.session import AsyncSessionFactory
from app.services import equipment_asset_service as asset_svc
from app.services import teacher_adjustment_service as svc

HALL_INV = UUID("02f3b836-1507-402c-983c-de4d468a6bff")
HALL_IDS = [
    UUID("0a1b2c3d-0000-4000-8000-000000000001"),  # placeholder, resolved below
]
TEACHER = UUID("8a1ca28e-83aa-5df3-b594-09def09c7d31")
ACTOR = UUID("02713d60-5dc1-5e0f-bc52-bb609f71c5b7")
ISSUE_IDS: list[UUID] = []  # filled by cleanup and left for later cleanup


async def db_state(label: str) -> None:
    async with AsyncSessionFactory() as s:
        inv = (await s.execute(text(
            "SELECT usable_quantity, disabled_quantity, total_quantity "
            "FROM lab_equipment_inventory WHERE id = :i"
        ), {"i": HALL_INV})).one()
        statuses = {
            r[0]: r[1]
            for r in (await s.execute(text(
                "SELECT status, count(*) FROM equipment_asset "
                "WHERE current_inventory_id = :i GROUP BY status"
            ), {"i": HALL_INV})).all()
        }
        issues = (await s.execute(text(
            "SELECT status, count(*) FROM resource_issue_report "
            "WHERE created_at >= now() - interval '2 hours' GROUP BY status"
        ))).all()
        print(f"[{label}] inv={inv} statuses={statuses} recent_issues={list(issues)}")


async def cleanup_all() -> None:
    async with AsyncSessionFactory() as s:
        issue_rows = (await s.execute(text(
            "SELECT id FROM resource_issue_report WHERE created_at >= now() - interval '2 hours'"
        ))).all()
        for (issue_id,) in issue_rows:
            await s.execute(text("DELETE FROM resource_issue_report WHERE id = :i"), {"i": issue_id})
        await s.execute(text(
            "UPDATE equipment_asset SET status = 'AVAILABLE', updated_by = :a "
            "WHERE current_inventory_id = :i"
        ), {"a": ACTOR, "i": HALL_INV})
        await s.execute(text(
            "UPDATE lab_equipment_inventory SET usable_quantity = 0, disabled_quantity = 0 "
            "WHERE id = :i"
        ), {"i": HALL_INV})
        await s.commit()
    async with AsyncSessionFactory() as s:
        await asset_svc.sync_inventory_counts(s, HALL_INV)
        await s.commit()


async def hall_ids() -> list[UUID]:
    async with AsyncSessionFactory() as s:
        rows = (await s.execute(text(
            "SELECT id FROM equipment_asset WHERE current_inventory_id = :i "
            "ORDER BY instrument_no LIMIT 4"
        ), {"i": HALL_INV})).all()
        return [r[0] for r in rows]


async def create_issue(session, asset_id: UUID, i: int, *, issue_type: str = "EQUIPMENT_FAILURE"):
    return await asset_svc.create_asset_issue(
        session,
        teacher_id=TEACHER,
        actor_id=ACTOR,
        asset_id=asset_id,
        issue_type=issue_type,
        severity="NORMAL",
        description=f"repro sequence {i}",
        impact_start=datetime(2026, 7, 5, tzinfo=UTC),
        impact_end=datetime(2027, 1, 16, tzinfo=UTC),
    )


async def run_sequence(mode: str, asset_ids: list[UUID], *, issue_type: str = "EQUIPMENT_FAILURE") -> None:
    label = "scrap" if issue_type == "EQUIPMENT_SCRAP" else "failure"
    print(f"\n===== mode: {mode} ({label}) =====")
    for i, asset_id in enumerate(asset_ids, 1):
        async with AsyncSessionFactory() as s:
            issue, _ = await create_issue(s, asset_id, i, issue_type=issue_type)
        async with AsyncSessionFactory() as s:
            issue2, impact = await svc.review_resource_issue(
                s, issue_id=issue.id, actor_id=ACTOR,
                approved=True, approved_quantity=None,
            )
            print(f"  [{i}] {issue2.report_no} -> {issue2.status} "
                  f"shortage={impact.get('shortage')} "
                  f"available={impact.get('available')} required={impact.get('required')}")
            if issue2.status == "RELOCATION_REQUIRED":
                # 报废/故障对齐：生成方案后完成流转（报废 -> SCRAPPED，故障 -> PROCESSING）
                from app.schemas.student_consultation import SelectionPreferences
                from app.services.resource_relocation_service import (
                    generate_resource_relocation_plans,
                )
                plans = await generate_resource_relocation_plans(
                    s, issue_id=issue2.id, actor_id=ACTOR,
                    preferences=SelectionPreferences(), max_plans=3,
                )
                issue3 = await s.get(svc.ResourceIssueReport, issue2.id)
                print(f"      generate -> {len(plans)} plans, status={issue3.status}")
        await db_state(f"after {i}")


async def main() -> None:
    ids = await hall_ids()
    print("first 4 hall asset ids:", ids)
    await cleanup_all()
    await db_state("baseline")
    await run_sequence("independent", ids)
    await cleanup_all()
    await db_state("baseline2")
    await run_sequence("shared", ids)
    await cleanup_all()
    await db_state("baseline3")
    await run_sequence("scrap", ids, issue_type="EQUIPMENT_SCRAP")
    await db_state("final")


asyncio.run(main())
