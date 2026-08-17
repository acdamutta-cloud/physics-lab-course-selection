"""add single equipment asset tracking and scrap workflow

Revision ID: a7c9e2f4b610
Revises: f4b9a6c2d710
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "a7c9e2f4b610"
down_revision: str | Sequence[str] | None = "f4b9a6c2d710"
branch_labels = None
depends_on = None


def _base_columns() -> list[sa.Column]:
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True)),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True)),
    ]


def _drop_resource_issue_check_containing(bind, needle: str) -> None:
    names = list(
        bind.execute(
            sa.text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'resource_issue_report'::regclass "
                "AND contype = 'c' AND pg_get_constraintdef(oid) ILIKE :needle"
            ),
            {"needle": f"%{needle}%"},
        ).scalars()
    )
    if not names:
        raise RuntimeError(f"未找到 resource_issue_report 中包含 {needle} 的检查约束")
    for name in names:
        op.drop_constraint(op.f(name), "resource_issue_report", type_="check")


def upgrade() -> None:
    bind = op.get_bind()
    op.create_table(
        "equipment_asset",
        *_base_columns(),
        sa.Column("asset_code", sa.String(96), nullable=False),
        sa.Column("equipment_type_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("current_inventory_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("origin_laboratory_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("manufacturer_serial", sa.String(100)),
        sa.Column("purchase_date", sa.Date()),
        sa.Column("note", sa.Text()),
        sa.Column("status", sa.String(24), nullable=False),
        sa.ForeignKeyConstraint(["equipment_type_id"], ["equipment_type.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["current_inventory_id"], ["lab_equipment_inventory.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["origin_laboratory_id"], ["laboratory.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("asset_code", name="uq_equipment_asset_asset_code"),
        sa.CheckConstraint("status IN ('AVAILABLE','QUARANTINED','UNDER_REPAIR','DISABLED','LOST','SCRAPPED')", name="status_allowed"),
    )
    op.create_index("ix_equipment_asset_inventory_status", "equipment_asset", ["current_inventory_id", "status"])

    op.add_column("resource_issue_report", sa.Column("source_issue_id", postgresql.UUID(as_uuid=True)))
    op.create_foreign_key("fk_resource_issue_source", "resource_issue_report", "resource_issue_report", ["source_issue_id"], ["id"], ondelete="SET NULL")
    _drop_resource_issue_check_containing(bind, "EQUIPMENT_FAILURE")
    _drop_resource_issue_check_containing(bind, "PENDING_REVIEW")
    op.create_check_constraint("issue_type_allowed", "resource_issue_report", "issue_type IN ('EQUIPMENT_FAILURE','MATERIAL_SHORTAGE','LAB_UNAVAILABLE','ENVIRONMENT','EQUIPMENT_SCRAP','OTHER')")
    op.create_check_constraint("status_allowed", "resource_issue_report", "status IN ('REPORTED','PENDING_REVIEW','PROCESSING','RESOLVED','REJECTED','CLOSED','SCRAP_REVIEW','RELOCATION_REQUIRED','READY_TO_EXECUTE','SCRAPPED')")

    op.create_table(
        "resource_issue_asset",
        *_base_columns(),
        sa.Column("resource_issue_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("previous_status", sa.String(24), nullable=False),
        sa.ForeignKeyConstraint(["resource_issue_id"], ["resource_issue_report.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["equipment_asset.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "resource_issue_id", "asset_id",
            name="uq_resource_issue_asset_issue_asset",
        ),
    )
    op.create_index("uq_resource_issue_asset_active", "resource_issue_asset", ["asset_id"], unique=True, postgresql_where=sa.text("active = true"))
    op.create_table(
        "resource_issue_observation",
        *_base_columns(),
        sa.Column("resource_issue_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reporter_teacher_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["resource_issue_id"], ["resource_issue_report.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reporter_teacher_id"], ["teacher.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_resource_issue_observation_issue_time", "resource_issue_observation", ["resource_issue_id", "created_at"])
    op.create_table(
        "equipment_asset_event",
        *_base_columns(),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("from_status", sa.String(24)), sa.Column("to_status", sa.String(24)),
        sa.Column("from_inventory_id", postgresql.UUID(as_uuid=True)), sa.Column("to_inventory_id", postgresql.UUID(as_uuid=True)),
        sa.Column("resource_issue_id", postgresql.UUID(as_uuid=True)), sa.Column("note", sa.Text()),
        sa.ForeignKeyConstraint(["asset_id"], ["equipment_asset.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["from_inventory_id"], ["lab_equipment_inventory.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["to_inventory_id"], ["lab_equipment_inventory.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resource_issue_id"], ["resource_issue_report.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_equipment_asset_event_asset_time", "equipment_asset_event", ["asset_id", "created_at"])

    inventories = list(bind.execute(sa.text("SELECT i.id, i.laboratory_id, i.equipment_type_id, i.total_quantity, i.usable_quantity, i.disabled_quantity, l.lab_code, e.equipment_code FROM lab_equipment_inventory i JOIN laboratory l ON l.id=i.laboratory_id JOIN equipment_type e ON e.id=i.equipment_type_id ORDER BY l.lab_code,e.equipment_code")).mappings())
    invalid = [str(inv["id"]) for inv in inventories if inv["total_quantity"] != inv["usable_quantity"] + inv["disabled_quantity"]]
    if invalid:
        raise RuntimeError("以下汇总库存数量无法对平，请先核对：" + ", ".join(invalid))
    for inv in inventories:
        for number in range(1, inv["total_quantity"] + 1):
            asset_id = uuid4()
            asset_status = "AVAILABLE" if number <= inv["usable_quantity"] else "DISABLED"
            bind.execute(sa.text("INSERT INTO equipment_asset (id,asset_code,equipment_type_id,current_inventory_id,origin_laboratory_id,status) VALUES (:id,:code,:type_id,:inventory_id,:lab_id,:status)"), {
                "id": asset_id, "code": f'{inv["lab_code"]}-{inv["equipment_code"]}-{number:03d}',
                "type_id": inv["equipment_type_id"], "inventory_id": inv["id"], "lab_id": inv["laboratory_id"],
                "status": asset_status,
            })
            bind.execute(sa.text("INSERT INTO equipment_asset_event (id,asset_id,event_type,to_status,to_inventory_id,note) VALUES (:id,:asset_id,'REGISTER',:status,:inventory_id,'历史汇总库存拆分')"), {
                "id": uuid4(), "asset_id": asset_id, "status": asset_status,
                "inventory_id": inv["id"],
            })
    active_issues = bind.execute(sa.text("SELECT id, inventory_id, GREATEST(approved_quantity-restored_quantity, affected_quantity) AS quantity FROM resource_issue_report WHERE status='PROCESSING' AND inventory_id IS NOT NULL")).mappings()
    for issue in active_issues:
        assets = bind.execute(sa.text("SELECT id FROM equipment_asset WHERE current_inventory_id=:inventory_id AND status='DISABLED' ORDER BY asset_code LIMIT :quantity"), {"inventory_id": issue["inventory_id"], "quantity": issue["quantity"]}).scalars().all()
        if len(assets) != issue["quantity"]:
            raise RuntimeError(f'活动工单 {issue["id"]} 没有足够的停用资产可供回填')
        for asset_id in assets:
            bind.execute(sa.text("INSERT INTO resource_issue_asset (id,resource_issue_id,asset_id,active,previous_status) VALUES (:id,:issue_id,:asset_id,true,'DISABLED')"), {"id": uuid4(), "issue_id": issue["id"], "asset_id": asset_id})

    mismatches = bind.execute(sa.text("SELECT i.id FROM lab_equipment_inventory i LEFT JOIN equipment_asset a ON a.current_inventory_id=i.id GROUP BY i.id,i.total_quantity,i.usable_quantity,i.disabled_quantity HAVING i.total_quantity<>COUNT(a.id) OR i.usable_quantity<>COUNT(a.id) FILTER (WHERE a.status='AVAILABLE') OR i.disabled_quantity<>COUNT(a.id) FILTER (WHERE a.status NOT IN ('AVAILABLE','SCRAPPED'))")).scalars().all()
    if mismatches:
        raise RuntimeError("资产拆分后以下汇总库存无法对平：" + ", ".join(map(str, mismatches)))


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_table("equipment_asset_event")
    op.drop_table("resource_issue_observation")
    op.drop_table("resource_issue_asset")
    op.drop_constraint("fk_resource_issue_source", "resource_issue_report", type_="foreignkey")
    op.drop_column("resource_issue_report", "source_issue_id")
    _drop_resource_issue_check_containing(bind, "EQUIPMENT_FAILURE")
    _drop_resource_issue_check_containing(bind, "PENDING_REVIEW")
    op.create_check_constraint("issue_type_allowed", "resource_issue_report", "issue_type IN ('EQUIPMENT_FAILURE','MATERIAL_SHORTAGE','LAB_UNAVAILABLE','ENVIRONMENT','OTHER')")
    op.create_check_constraint("status_allowed", "resource_issue_report", "status IN ('REPORTED','PENDING_REVIEW','PROCESSING','RESOLVED','REJECTED','CLOSED')")
    op.drop_table("equipment_asset")
