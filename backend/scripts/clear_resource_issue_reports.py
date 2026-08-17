"""Preview or clear resource issue reports and their dependent workflow records.

The command is dry-run by default. Destructive execution requires both
``--execute`` and the explicit confirmation token.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from app.db.session import AsyncSessionFactory
from app.services.equipment_asset_service import sync_inventory_counts


CONFIRMATION_TOKEN = "DELETE-RESOURCE-ISSUES"


async def _counts(session) -> dict[str, int]:
    statements = {
        "resource_issue_report": "SELECT COUNT(*) FROM resource_issue_report",
        "resource_issue_asset": "SELECT COUNT(*) FROM resource_issue_asset",
        "resource_issue_observation": "SELECT COUNT(*) FROM resource_issue_observation",
        "resource_repair_update": "SELECT COUNT(*) FROM resource_repair_update",
        "equipment_inventory_movement": "SELECT COUNT(*) FROM equipment_inventory_movement",
        "resource_relocation_plan": "SELECT COUNT(*) FROM resource_relocation_plan",
        "resource_relocation_item": "SELECT COUNT(*) FROM resource_relocation_item",
        "linked_asset_events": (
            "SELECT COUNT(*) FROM equipment_asset_event "
            "WHERE resource_issue_id IS NOT NULL"
        ),
        "active_asset_links": (
            "SELECT COUNT(*) FROM resource_issue_asset WHERE active = true"
        ),
    }
    return {
        name: int(await session.scalar(text(statement)) or 0)
        for name, statement in statements.items()
    }


async def clear_resource_issues(*, execute: bool, confirmation: str) -> None:
    async with AsyncSessionFactory() as session:
        before = await _counts(session)
        print({"mode": "execute" if execute else "dry-run", "before": before})
        if not execute:
            return
        if confirmation != CONFIRMATION_TOKEN:
            raise SystemExit(
                f"Refusing destructive execution: pass --confirm {CONFIRMATION_TOKEN}"
            )

        inventory_ids = list(
            (
                await session.execute(
                    text(
                        "SELECT DISTINCT a.current_inventory_id "
                        "FROM equipment_asset a "
                        "JOIN resource_issue_asset ria ON ria.asset_id = a.id"
                    )
                )
            ).scalars()
        )

        # Restore instruments still held by an active workflow to the state they
        # had before their earliest report. Completed scraps remain scrapped.
        await session.execute(
            text(
                """
                UPDATE equipment_asset AS asset
                SET status = original.previous_status, updated_at = now()
                FROM (
                    SELECT DISTINCT ON (asset_id) asset_id, previous_status
                    FROM resource_issue_asset
                    ORDER BY asset_id, created_at ASC
                ) AS original
                WHERE asset.id = original.asset_id
                  AND asset.status <> 'SCRAPPED'
                  AND EXISTS (
                      SELECT 1 FROM resource_issue_asset active_link
                      WHERE active_link.asset_id = asset.id
                        AND active_link.active = true
                  )
                """
            )
        )
        await session.execute(
            text("DELETE FROM equipment_asset_event WHERE resource_issue_id IS NOT NULL")
        )
        await session.execute(text("DELETE FROM equipment_inventory_movement"))
        await session.execute(text("DELETE FROM resource_issue_report"))

        for inventory_id in inventory_ids:
            await sync_inventory_counts(session, inventory_id)
        await session.commit()

        after = await _counts(session)
        print({"deleted": before, "after": after})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    asyncio.run(
        clear_resource_issues(execute=args.execute, confirmation=args.confirm)
    )


if __name__ == "__main__":
    main()
