"""Remove all test resource issues (HALL inventory) and restore assets.

Deletes resource_issue_report rows referencing the HALL inventory (cascades
to issue_asset/observation/relocation_plan/relocation_item), resets the
affected assets to AVAILABLE, and re-syncs inventory counts to the 23-asset
baseline.
"""
import asyncio
from uuid import UUID

from sqlalchemy import text

from app.db.session import AsyncSessionFactory
from app.models.resources import LabEquipmentInventory
from app.services import equipment_asset_service as asset_svc

HALL_INV = UUID("02f3b836-1507-402c-983c-de4d468a6bff")
ACTOR = UUID("02713d60-5dc1-5e0f-bc52-bb609f71c5b7")


async def main() -> None:
    async with AsyncSessionFactory() as s:
        issue_ids = (
            await s.execute(
                text(
                    "SELECT id FROM resource_issue_report WHERE inventory_id = :i"
                ),
                {"i": HALL_INV},
            )
        ).scalars()
        issue_list = list(issue_ids)
        print("deleting issues:", len(issue_list))
        for issue_id in issue_list:
            await s.execute(
                text("DELETE FROM resource_issue_report WHERE id = :i"),
                {"i": issue_id},
            )
        result = await s.execute(
            text(
                "UPDATE equipment_asset SET status = 'AVAILABLE', updated_by = :a "
                "WHERE current_inventory_id = :i"
            ),
            {"a": ACTOR, "i": HALL_INV},
        )
        print("assets restored:", result.rowcount)
        await s.commit()
    async with AsyncSessionFactory() as s:
        inv = await s.get(LabEquipmentInventory, HALL_INV)
        await asset_svc.sync_inventory_counts(s, HALL_INV)
        await s.commit()
        print(
            "inventory now:",
            inv.usable_quantity, inv.disabled_quantity, inv.total_quantity,
        )
    async with AsyncSessionFactory() as s:
        remaining = (await s.execute(text(
            "SELECT count(*) FROM resource_issue_report"
        ))).scalar_one()
        statuses = {
            r[0]: r[1] for r in (await s.execute(text(
                "SELECT status, count(*) FROM equipment_asset "
                "WHERE current_inventory_id = :i GROUP BY status"
            ), {"i": HALL_INV})).all()
        }
        print("remaining issues total:", remaining, "| hall statuses:", statuses)


asyncio.run(main())
