"""add_student_course_completion

Revision ID: 28cc400ecde2
Revises: 84bd2c5e7f10
Create Date: 2026-08-03 09:55:39.257468
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '28cc400ecde2'
down_revision: str | Sequence[str] | None = '84bd2c5e7f10'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('student_course_completion',
    sa.Column('student_id', sa.Uuid(), nullable=False),
    sa.Column('course_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('updated_by', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('PASSED', 'FAILED', 'IN_PROGRESS')", name=op.f('ck_student_course_completion_completion_status_allowed')),
    sa.ForeignKeyConstraint(['course_id'], ['experiment_course.id'], name=op.f('fk_student_course_completion_course_id_experiment_course'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['student_id'], ['student.id'], name=op.f('fk_student_course_completion_student_id_student'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_student_course_completion')),
    sa.UniqueConstraint('student_id', 'course_id', name='student_course')
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    op.drop_table('student_course_completion')
