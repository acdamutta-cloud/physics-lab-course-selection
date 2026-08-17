"""drop unused agent and availability tables

删除从未被业务代码使用的 5 张孤儿表：
agent_run / agent_step_log / agent_feedback / prompt_template / teacher_availability
（通知走 Redis 队列，notification 表保留。）

Revision ID: a2f9c8e4d650
Revises: d1e3f5a7b902
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 'a2f9c8e4d650'
down_revision: str | Sequence[str] | None = 'd1e3f5a7b902'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 先删依赖 agent_run 的子表，再删 agent_run
    op.drop_index('ix_agent_step_log_run_time', table_name='agent_step_log')
    op.drop_table('agent_step_log')
    op.drop_index('ix_agent_feedback_run', table_name='agent_feedback')
    op.drop_table('agent_feedback')
    op.drop_index('ix_agent_run_requester_status', table_name='agent_run')
    op.drop_table('agent_run')
    op.drop_index('ix_prompt_template_agent_status', table_name='prompt_template')
    op.drop_table('prompt_template')
    op.drop_index('ix_teacher_availability_lookup', table_name='teacher_availability')
    op.drop_table('teacher_availability')


def downgrade() -> None:
    op.create_table('agent_run',
    sa.Column('thread_id', sa.String(length=128), nullable=False),
    sa.Column('graph_name', sa.String(length=100), nullable=False),
    sa.Column('graph_version', sa.String(length=32), nullable=False),
    sa.Column('requester_user_id', sa.Uuid(), nullable=False),
    sa.Column('business_type', sa.String(length=64), nullable=True),
    sa.Column('business_id', sa.Uuid(), nullable=True),
    sa.Column('prompt_versions', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('PENDING', 'RUNNING', 'INTERRUPTED', 'SUCCEEDED', 'FAILED', 'CANCELLED')", name=op.f('ck_agent_run_status_allowed')),
    sa.ForeignKeyConstraint(['requester_user_id'], ['user_account.id'], name=op.f('fk_agent_run_requester_user_id_user_account'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_run')),
    sa.UniqueConstraint('thread_id', name=op.f('uq_agent_run_thread_id'))
    )
    op.create_index('ix_agent_run_requester_status', 'agent_run', ['requester_user_id', 'status'], unique=False)
    op.create_table('agent_feedback',
    sa.Column('run_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('rating', sa.Integer(), nullable=False),
    sa.Column('feedback_type', sa.String(length=64), nullable=True),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('rating BETWEEN 1 AND 5', name=op.f('ck_agent_feedback_rating_valid')),
    sa.ForeignKeyConstraint(['run_id'], ['agent_run.id'], name=op.f('fk_agent_feedback_run_id_agent_run'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['user_account.id'], name=op.f('fk_agent_feedback_user_id_user_account'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_feedback'))
    )
    op.create_index('ix_agent_feedback_run', 'agent_feedback', ['run_id'], unique=False)
    op.create_table('agent_step_log',
    sa.Column('run_id', sa.Uuid(), nullable=False),
    sa.Column('node_name', sa.String(length=100), nullable=False),
    sa.Column('agent_code', sa.String(length=64), nullable=True),
    sa.Column('input_summary', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('tool_calls', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('output_summary', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('token_usage', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'INTERRUPTED', 'SKIPPED')", name=op.f('ck_agent_step_log_status_allowed')),
    sa.ForeignKeyConstraint(['run_id'], ['agent_run.id'], name=op.f('fk_agent_step_log_run_id_agent_run'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_step_log'))
    )
    op.create_index('ix_agent_step_log_run_time', 'agent_step_log', ['run_id', 'created_at'], unique=False)
    op.create_table('prompt_template',
    sa.Column('agent_code', sa.String(length=64), nullable=False),
    sa.Column('version_no', sa.String(length=32), nullable=False),
    sa.Column('system_prompt', sa.Text(), nullable=False),
    sa.Column('input_schema', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('output_schema', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('prompt_hash', sa.String(length=128), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('published_by', sa.Uuid(), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('updated_by', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')", name=op.f('ck_prompt_template_status_allowed')),
    sa.ForeignKeyConstraint(['published_by'], ['user_account.id'], name=op.f('fk_prompt_template_published_by_user_account'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_prompt_template')),
    sa.UniqueConstraint('agent_code', 'version_no', name='agent_version')
    )
    op.create_index('ix_prompt_template_agent_status', 'prompt_template', ['agent_code', 'status'], unique=False)
    op.create_table('teacher_availability',
    sa.Column('teacher_id', sa.Uuid(), nullable=False),
    sa.Column('term_id', sa.Uuid(), nullable=False),
    sa.Column('week_start', sa.SmallInteger(), nullable=False),
    sa.Column('week_end', sa.SmallInteger(), nullable=False),
    sa.Column('day_of_week', sa.SmallInteger(), nullable=False),
    sa.Column('start_slot', sa.SmallInteger(), nullable=False),
    sa.Column('end_slot', sa.SmallInteger(), nullable=False),
    sa.Column('availability_type', sa.String(length=20), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("availability_type IN ('AVAILABLE', 'UNAVAILABLE', 'PREFERRED')", name=op.f('ck_teacher_availability_availability_type_allowed')),
    sa.CheckConstraint('day_of_week BETWEEN 1 AND 7', name=op.f('ck_teacher_availability_day_valid')),
    sa.CheckConstraint('end_slot >= start_slot', name=op.f('ck_teacher_availability_slot_range_valid')),
    sa.CheckConstraint('start_slot >= 1', name=op.f('ck_teacher_availability_start_slot_positive')),
    sa.CheckConstraint('week_end >= week_start', name=op.f('ck_teacher_availability_week_range_valid')),
    sa.CheckConstraint('week_start >= 1', name=op.f('ck_teacher_availability_week_start_positive')),
    sa.ForeignKeyConstraint(['teacher_id'], ['teacher.id'], name=op.f('fk_teacher_availability_teacher_id_teacher'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['term_id'], ['academic_term.id'], name=op.f('fk_teacher_availability_term_id_academic_term'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_teacher_availability'))
    )
    op.create_index('ix_teacher_availability_lookup', 'teacher_availability', ['teacher_id', 'term_id', 'day_of_week'], unique=False)
