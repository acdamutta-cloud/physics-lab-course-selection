"""merge equipment asset identifiers into instrument_no

Revision ID: b8d3f1a6c920
Revises: a7c9e2f4b610
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8d3f1a6c920"
down_revision: str | Sequence[str] | None = "a7c9e2f4b610"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    unique_constraints = bind.execute(
        sa.text(
            """
            SELECT constraint_name
            FROM information_schema.constraint_column_usage
            WHERE table_schema = current_schema()
              AND table_name = 'equipment_asset'
              AND column_name = 'asset_code'
              AND constraint_name IN (
                  SELECT constraint_name
                  FROM information_schema.table_constraints
                  WHERE table_schema = current_schema()
                    AND table_name = 'equipment_asset'
                    AND constraint_type = 'UNIQUE'
              )
            """
        )
    ).scalars().all()
    for constraint_name in unique_constraints:
        op.drop_constraint(constraint_name, "equipment_asset", type_="unique")

    op.alter_column("equipment_asset", "asset_code", new_column_name="instrument_no")
    op.drop_column("equipment_asset", "manufacturer_serial")
    op.create_unique_constraint(
        "uq_equipment_asset_instrument_no", "equipment_asset", ["instrument_no"]
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_equipment_asset_instrument_no", "equipment_asset", type_="unique"
    )
    op.add_column(
        "equipment_asset", sa.Column("manufacturer_serial", sa.String(100), nullable=True)
    )
    op.alter_column("equipment_asset", "instrument_no", new_column_name="asset_code")
    op.create_unique_constraint(
        "uq_equipment_asset_asset_code", "equipment_asset", ["asset_code"]
    )
