"""drop unused columns

删除从未被业务代码读写的 23 个冗余字段（字段级审计确认，
无前端展示、无后端读写、无 schema 序列化引用）。

Revision ID: b3c5a7d9e1f0
Revises: a2f9c8e4d650
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 'b3c5a7d9e1f0'
down_revision: str | Sequence[str] | None = 'a2f9c8e4d650'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 规则库：rule_config 的 JSON 配置字段从未被规则引擎读取
    op.drop_column('rule_config', 'scope_config')
    op.drop_column('rule_config', 'condition_config')
    op.drop_column('rule_config', 'action_config')
    # rule_set：唯一约束 (rule_domain, rule_set_code, version_no) 与发布索引
    op.execute("ALTER TABLE rule_set DROP CONSTRAINT IF EXISTS domain_code_version")
    op.drop_index('uq_rule_set_published', table_name='rule_set')
    op.drop_column('rule_set', 'rule_set_code')
    op.create_unique_constraint('domain_code_version', 'rule_set', ['rule_domain', 'version_no'])
    op.create_index('uq_rule_set_published', 'rule_set', ['rule_domain'], unique=True,
                    postgresql_where=sa.text("status = 'PUBLISHED'"))
    # 申请单：申请级规则集外键（审批使用全局规则集）。
    # domain guard 触发器（d6b8f2a4c901 创建，函数被 schedule_job/schedule_version/
    # selection_window 共用）挂在待删列上，必须先 drop；函数保留。
    op.execute('DROP TRIGGER IF EXISTS trg_application_adjustment_rule_domain '
               'ON application_request')
    op.execute('DROP TRIGGER IF EXISTS trg_application_approval_rule_domain '
               'ON application_request')
    # 外键此前已被手动删除，使用 IF EXISTS 幂等处理
    op.execute('ALTER TABLE application_request DROP CONSTRAINT IF EXISTS '
               'fk_application_request_adjustment_rule_set_id_rule_set')
    op.execute('ALTER TABLE application_request DROP CONSTRAINT IF EXISTS '
               'fk_application_request_approval_rule_set_id_rule_set')
    op.drop_column('application_request', 'adjustment_rule_set_id')
    op.drop_column('application_request', 'approval_rule_set_id')
    # 审计日志：IP 地址预留
    op.drop_column('operation_log', 'ip_address')
    # 学生/班级基础信息预留
    op.drop_column('student', 'gender')
    op.drop_column('student', 'birth_date')
    op.drop_column('campus', 'address')
    op.drop_column('major', 'degree_type')
    # 课程/项目预留属性
    op.execute("ALTER TABLE experiment_course DROP CONSTRAINT IF EXISTS credits_nonnegative")
    op.drop_column('experiment_course', 'credits')
    op.drop_column('experiment_course', 'default_slots')
    op.drop_column('experiment_project', 'material_note')
    # 修读记录：缺做原因与完成时间（状态字段已表达）
    op.drop_column('student_project_record', 'absence_reason')
    op.drop_column('student_project_record', 'completed_at')
    # 排课版本链：parent_version_id 预留
    op.drop_column('schedule_version', 'parent_version_id')
    # 教学任务容量缓冲参数预留
    op.execute("ALTER TABLE teaching_task DROP CONSTRAINT IF EXISTS buffer_ratio_valid")
    op.drop_column('teaching_task', 'capacity_buffer_ratio')
    # 培养方案有效期（仅使用生效起始日）
    op.drop_column('training_plan', 'effective_to')
    # 设备台账预留
    op.drop_column('equipment_asset', 'purchase_date')
    op.drop_column('lab_equipment_inventory', 'checked_at')
    op.drop_column('teacher_project_qualification', 'valid_from')
    op.drop_column('teacher_project_qualification', 'valid_to')


def downgrade() -> None:
    # 学生/班级基础信息
    op.add_column('student', sa.Column('gender', sa.String(length=16), nullable=True))
    op.add_column('student', sa.Column('birth_date', sa.Date(), nullable=True))
    op.add_column('campus', sa.Column('address', sa.String(length=255), nullable=True))
    op.add_column('major', sa.Column('degree_type', sa.String(length=32), nullable=False,
                                     server_default='ENGINEERING'))
    # 课程/项目属性
    op.add_column('experiment_course', sa.Column('credits', sa.Numeric(precision=4, scale=1),
                                                 nullable=False, server_default='1.0'))
    op.add_column('experiment_course', sa.Column('default_slots', sa.SmallInteger(),
                                                 nullable=False, server_default='4'))
    op.create_check_constraint('credits_nonnegative', 'experiment_course', 'credits >= 0')
    op.add_column('experiment_project', sa.Column('material_note', sa.Text(), nullable=True))
    # 修读记录
    op.add_column('student_project_record', sa.Column('absence_reason', sa.Text(), nullable=True))
    op.add_column('student_project_record', sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True))
    # 排课版本链
    op.add_column('schedule_version', sa.Column('parent_version_id', sa.Uuid(), nullable=True))
    # 教学任务缓冲参数
    op.add_column('teaching_task', sa.Column('capacity_buffer_ratio', sa.Numeric(precision=5, scale=2),
                                             nullable=False, server_default='1.20'))
    op.create_check_constraint('buffer_ratio_valid', 'teaching_task', 'capacity_buffer_ratio >= 1')
    # 培养方案
    op.add_column('training_plan', sa.Column('effective_to', sa.Date(), nullable=True))
    # 设备台账
    op.add_column('equipment_asset', sa.Column('purchase_date', sa.Date(), nullable=True))
    op.add_column('lab_equipment_inventory', sa.Column('checked_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('teacher_project_qualification', sa.Column('valid_from', sa.Date(), nullable=True))
    op.add_column('teacher_project_qualification', sa.Column('valid_to', sa.Date(), nullable=True))
    # 申请单规则集外键
    op.add_column('application_request', sa.Column('adjustment_rule_set_id', sa.Uuid(), nullable=True))
    op.add_column('application_request', sa.Column('approval_rule_set_id', sa.Uuid(), nullable=True))
    # 重建 domain guard 触发器（enforce_rule_set_domain 函数仍被其他表使用）
    op.execute(
        "CREATE TRIGGER trg_application_adjustment_rule_domain "
        "BEFORE INSERT OR UPDATE OF adjustment_rule_set_id "
        "ON application_request FOR EACH ROW "
        "EXECUTE FUNCTION enforce_rule_set_domain("
        "'adjustment_rule_set_id', 'ADJUSTMENT')"
    )
    op.execute(
        "CREATE TRIGGER trg_application_approval_rule_domain "
        "BEFORE INSERT OR UPDATE OF approval_rule_set_id "
        "ON application_request FOR EACH ROW "
        "EXECUTE FUNCTION enforce_rule_set_domain("
        "'approval_rule_set_id', 'APPROVAL')"
    )
    # 审计日志
    op.add_column('operation_log', sa.Column('ip_address', postgresql.INET(), nullable=True))
    # 规则库
    op.drop_constraint('domain_code_version', 'rule_set', type_='unique')
    op.drop_index('uq_rule_set_published', table_name='rule_set')
    op.add_column('rule_set', sa.Column('rule_set_code', sa.String(length=64), nullable=False,
                                        server_default='LEGACY'))
    op.create_unique_constraint('domain_code_version', 'rule_set',
                                ['rule_domain', 'rule_set_code', 'version_no'])
    op.create_index('uq_rule_set_published', 'rule_set', ['rule_domain', 'rule_set_code'],
                    unique=True, postgresql_where=sa.text("status = 'PUBLISHED'"))
    op.add_column('rule_config', sa.Column('scope_config', postgresql.JSONB(astext_type=sa.Text()),
                                           nullable=False, server_default='{}'))
    op.add_column('rule_config', sa.Column('condition_config', postgresql.JSONB(astext_type=sa.Text()),
                                           nullable=False, server_default='{}'))
    op.add_column('rule_config', sa.Column('action_config', postgresql.JSONB(astext_type=sa.Text()),
                                           nullable=False, server_default='{}'))
