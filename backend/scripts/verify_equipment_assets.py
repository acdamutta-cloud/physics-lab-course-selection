"""Read-only database verification for the single-instrument asset migration."""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.db.session import AsyncSessionFactory


async def main() -> None:
    async with AsyncSessionFactory() as session:
        tables = list(
            (
                await session.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema='public' AND table_name IN "
                        "('equipment_asset','equipment_asset_event',"
                        "'resource_issue_asset','resource_issue_observation',"
                        "'operation_guide_index') ORDER BY table_name"
                    )
                )
            ).scalars()
        )
        version = await session.scalar(text("SELECT version_num FROM alembic_version"))
        source_column = await session.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='resource_issue_report' "
                "AND column_name='source_issue_id')"
            )
        )
        asset_columns = list(
            (
                await session.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='equipment_asset' "
                        "ORDER BY ordinal_position"
                    )
                )
            ).scalars()
        )
        print(
            {
                "alembic_version": version,
                "tables": tables,
                "source_issue_id": source_column,
                "asset_identifier_columns": [
                    value
                    for value in asset_columns
                    if value in {"instrument_no", "asset_code", "manufacturer_serial"}
                ],
            }
        )
        if "equipment_asset" not in tables:
            return
        counts = (
            await session.execute(
                text(
                    "SELECT (SELECT COUNT(*) FROM equipment_asset) AS assets, "
                    "(SELECT COUNT(*) FROM equipment_asset_event) AS events, "
                    "(SELECT COUNT(*) FROM resource_issue_asset) AS issue_links"
                )
            )
        ).mappings().one()
        mismatches = list(
            (
                await session.execute(
                    text(
                        "SELECT i.id FROM lab_equipment_inventory i "
                        "LEFT JOIN equipment_asset a ON a.current_inventory_id=i.id "
                        "GROUP BY i.id,i.total_quantity,i.usable_quantity,i.disabled_quantity "
                        "HAVING i.total_quantity<>COUNT(a.id) FILTER (WHERE a.status<>'SCRAPPED') "
                        "OR i.usable_quantity<>COUNT(a.id) FILTER (WHERE a.status='AVAILABLE') "
                        "OR i.disabled_quantity<>COUNT(a.id) FILTER "
                        "(WHERE a.status NOT IN ('AVAILABLE','SCRAPPED'))"
                    )
                )
            ).scalars()
        )
        print({"counts": dict(counts), "inventory_mismatches": [str(value) for value in mismatches]})
        samples = list(
            (
                await session.execute(
                    text(
                        "SELECT l.name AS laboratory_name, e.instrument_no, e.status "
                        "FROM equipment_asset e "
                        "JOIN lab_equipment_inventory i ON i.id=e.current_inventory_id "
                        "JOIN laboratory l ON l.id=i.laboratory_id "
                        "WHERE l.name LIKE '%B101%' ORDER BY e.instrument_no LIMIT 5"
                    )
                )
            ).mappings()
        )
        print({"B101_samples": [dict(value) for value in samples]})


if __name__ == "__main__":
    asyncio.run(main())
