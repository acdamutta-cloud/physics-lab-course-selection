"""清除 lab_project_capability 种子写死容量的干扰。

种子数据(已随 seed 脚本删除,DB 遗留 32 条)给每个 项目x实验室 写死了
4/8/12/16/20/24 的 effective_capacity,与真实资源(实验室安全容量、设备
库存与共享人数)脱节,导致排课场次容量被压到极低值(如 X射线特征谱 C302
被压到 4,实验室实际可容纳 16)。

本脚本:
1. 按真实资源重算每个 (project, lab) 的有效容量:
   min(实验室 safety_capacity, 各必需设备容量), 不读 capability 当前值;
2. 更新 lab_project_capability.effective_capacity;
3. 同步更新该 (project, lab) 下所有 experiment_session.capacity,
   且不低于场次当前已选人数(防缩容超选)。
"""

import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text

from app.db.session import AsyncSessionFactory


async def main() -> None:
    async with AsyncSessionFactory() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT c.project_id, c.laboratory_id,
                           l.safety_capacity,
                           p.group_mode, p.default_group_size
                    FROM lab_project_capability c
                    JOIN laboratory l ON l.id = c.laboratory_id
                    JOIN experiment_project p ON p.id = c.project_id
                    """
                )
            )
        ).mappings().all()
        if not rows:
            print("NO CAPABILITY ROWS")
            return

        # 每个 (project, lab) 的必需设备与库存
        inventory_rows = (
            await session.execute(
                text(
                    """
                    SELECT i.laboratory_id, i.equipment_type_id,
                           i.usable_quantity, i.students_per_unit,
                           i.sharing_rule_status
                    FROM lab_equipment_inventory i
                    """
                )
            )
        ).mappings().all()
        requirement_rows = (
            await session.execute(
                text(
                    """
                    SELECT r.project_id, r.equipment_type_id,
                           r.units_per_group, r.required
                    FROM project_equipment_requirement r
                    WHERE r.required = true
                    """
                )
            )
        ).mappings().all()

        inventory_by_lab: dict[Any, dict[Any, dict[str, Any]]] = {}
        for row in inventory_rows:
            inventory_by_lab.setdefault(row["laboratory_id"], {})[
                row["equipment_type_id"]
            ] = row
        req_by_project: dict[Any, list[dict[str, Any]]] = {}
        for row in requirement_rows:
            req_by_project.setdefault(row["project_id"], []).append(row)

        updates: list[dict[str, Any]] = []
        for row in rows:
            lab_capacity = int(row["safety_capacity"])
            group_size = max(1, int(row["default_group_size"]))
            equipment_capacities: list[int] = []
            warnings: list[str] = []
            for req in req_by_project.get(row["project_id"], []):
                inventory = inventory_by_lab.get(
                    row["laboratory_id"], {}
                ).get(req["equipment_type_id"])
                if inventory is None:
                    equipment_capacities.append(0)
                    warnings.append(f"缺少必需设备配置 {req['equipment_type_id']}")
                    continue
                usable = int(inventory["usable_quantity"])
                if (
                    inventory["sharing_rule_status"] == "CONFIRMED"
                    and inventory["students_per_unit"]
                ):
                    capacity = usable * int(inventory["students_per_unit"])
                else:
                    units = max(1, int(req["units_per_group"]))
                    capacity = (usable // units) * group_size
                equipment_capacities.append(capacity)
            computed = min([lab_capacity, *equipment_capacities])
            if row["group_mode"] == "GROUP":
                computed -= computed % group_size
            computed = max(0, computed)
            updates.append(
                {
                    "project_id": row["project_id"],
                    "laboratory_id": row["laboratory_id"],
                    "seed_unknown": None,
                    "computed": computed,
                    "warnings": warnings,
                }
            )

        # 当前 seed 值 + 场次已选峰值,用于对比与缩容保护
        current = {
            (r["project_id"], r["laboratory_id"]): r["effective_capacity"]
            for r in (
                await session.execute(
                    text(
                        """
                        SELECT project_id, laboratory_id, effective_capacity
                        FROM lab_project_capability
                        """
                    )
                )
            ).mappings().all()
        }
        max_selected = {
            (r["project_id"], r["laboratory_id"]): int(r["m"] or 0)
            for r in (
                await session.execute(
                    text(
                        """
                        SELECT project_id, laboratory_id, MAX(selected_count) AS m
                        FROM experiment_session
                        GROUP BY project_id, laboratory_id
                        """
                    )
                )
            ).mappings().all()
        }
        lab_names = {
            r["id"]: r["name"]
            for r in (
                await session.execute(text("SELECT id, name FROM laboratory"))
            ).mappings().all()
        }
        project_names = {
            r["id"]: r["project_name"]
            for r in (
                await session.execute(
                    text("SELECT id, project_name FROM experiment_project")
                )
            ).mappings().all()
        }

        print(
            f"{'项目':<26}{'实验室':<20}{'seed':>5}{'真实':>5}{'已选峰值':>7}  结果"
        )
        for update in updates:
            key = (update["project_id"], update["laboratory_id"])
            seed = current.get(key)
            peak = max_selected.get(key, 0)
            final_cap = max(update["computed"], peak)
            print(
                f"{project_names.get(update['project_id'], '?')[:24]:<26}"
                f"{lab_names.get(update['laboratory_id'], '?')[:18]:<20}"
                f"{seed!s:>5}{update['computed']:>5}{peak:>7}  "
                f"-> {final_cap}"
                + (f"  WARN: {'; '.join(update['warnings'])}" if update["warnings"] else "")
            )
            update["final_cap"] = final_cap

        print("\n执行 UPDATE ...")
        for update in updates:
            await session.execute(
                text(
                    """
                    UPDATE lab_project_capability
                    SET effective_capacity = :cap
                    WHERE project_id = :pid AND laboratory_id = :lid
                    """
                ),
                {
                    "cap": update["final_cap"],
                    "pid": update["project_id"],
                    "lid": update["laboratory_id"],
                },
            )
            await session.execute(
                text(
                    """
                    UPDATE experiment_session
                    SET capacity = :cap
                    WHERE project_id = :pid AND laboratory_id = :lid
                      AND capacity != :cap
                    """
                ),
                {
                    "cap": update["final_cap"],
                    "pid": update["project_id"],
                    "lid": update["laboratory_id"],
                },
            )
        await session.commit()
        print("DONE")


if __name__ == "__main__":
    asyncio.run(main())
