"""Debug the review flow for an asset-backed issue.

Prints internal session state around review_resource_issue to pinpoint
whether sync_inventory_counts runs before resource_impact and what usable
quantity the impact calculation sees.
"""
import asyncio
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text

from app.db.session import AsyncSessionFactory
from app.models.resources import LabEquipmentInventory
from app.services import equipment_asset_service as asset_svc
from app.services import teacher_adjustment_service as svc

HALL_INV = UUID("02f3b836-1507-402c-983c-de4d468a6bff")
HALL_005 = UUID("98565aff-e805-4245-bf34-57ce1f1a052d")
TEACHER = UUID("8a1ca28e-83aa-5df3-b594-09def09c7d31")
ACTOR = UUID("02713d60-5dc1-5e0f-bc52-bb609f71c5b7")


async def db_usable() -> int:
    async with AsyncSessionFactory() as s:
        return int(
            (
                await s.execute(
                    text(
                        "SELECT usable_quantity FROM lab_equipment_inventory WHERE id = :i"
                    ),
                    {"i": HALL_INV},
                )
            ).scalar_one()
        )


async def main() -> None:
    print("== baseline: db usable =", await db_usable())
    issue_id: UUID | None = None
    async with AsyncSessionFactory() as s:
        issue, _dup = await asset_svc.create_asset_issue(
            s,
            teacher_id=TEACHER,
            actor_id=ACTOR,
            asset_id=HALL_005,
            issue_type="EQUIPMENT_FAILURE",
            severity="NORMAL",
            description="debug review flow",
            impact_start=datetime(2026, 7, 5, tzinfo=UTC),
            impact_end=datetime(2027, 1, 16, tzinfo=UTC),
        )
        issue_id = issue.id
    print("== created issue", issue_id, "| db usable =", await db_usable())

    async with AsyncSessionFactory() as s:
        issue = await s.get(svc.ResourceIssueReport, issue_id)
        inv = await s.get(LabEquipmentInventory, HALL_INV)
        print("== before review: session inventory usable =", inv.usable_quantity)
        rows = await asset_svc.assets_for_issue(s, issue_id)
        print("== assets_for_issue len =", len(rows))
        for asset, link in rows:
            print("   asset", asset.instrument_no, "status =", asset.status,
                  "| link active =", link.active, "| asset in session id map =",
                  asset.id in s.identity_map)
        # replay restore_or_transition manually to watch inventory object
        await asset_svc.restore_or_transition_issue_asset(
            s, issue, actor_id=ACTOR, target_status="UNDER_REPAIR", close_link=False
        )
        inv2 = await s.get(LabEquipmentInventory, HALL_INV)
        print("== after restore (same session): inventory usable =", inv2.usable_quantity,
              "| same object =", inv2 is inv)
        # roll back the manual restore; the real review will redo it
        issue.status = "PENDING_REVIEW"
        await s.rollback()

    print("== after rollback: db usable =", await db_usable())

    async with AsyncSessionFactory() as s:
        issue, impact = await svc.review_resource_issue(
            s, issue_id=issue_id, actor_id=ACTOR, approved=True, approved_quantity=None
        )
        inv = await s.get(LabEquipmentInventory, HALL_INV)
        print("== after review: issue.status =", issue.status,
              "| remediation_status =", issue.remediation_status)
        print("   impact =", {k: impact.get(k) for k in (
            "shortage", "available", "required", "total_required_relocation_count",
            "course_count", "session_count")})
        print("   session inventory usable =", inv.usable_quantity)
    print("== after review session close: db usable =", await db_usable())


asyncio.run(main())
