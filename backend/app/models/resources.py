from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AuditMixin, BaseModel


class TeacherProjectQualification(AuditMixin, BaseModel):
    __tablename__ = "teacher_project_qualification"
    __table_args__ = (
        UniqueConstraint(
            "teacher_id", "project_id", name="teacher_project"
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'EXPIRED', 'SUSPENDED')",
            name="status_allowed",
        ),
    )

    teacher_id: Mapped[UUID] = mapped_column(
        ForeignKey("teacher.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_project.id", ondelete="CASCADE"),
        nullable=False,
    )
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ACTIVE"
    )


class TeacherAvailability(BaseModel):
    __tablename__ = "teacher_availability"
    __table_args__ = (
        CheckConstraint("week_start >= 1", name="week_start_positive"),
        CheckConstraint("week_end >= week_start", name="week_range_valid"),
        CheckConstraint("day_of_week BETWEEN 1 AND 7", name="day_valid"),
        CheckConstraint("start_slot >= 1", name="start_slot_positive"),
        CheckConstraint("end_slot >= start_slot", name="slot_range_valid"),
        CheckConstraint(
            "availability_type IN ('AVAILABLE', 'UNAVAILABLE', 'PREFERRED')",
            name="availability_type_allowed",
        ),
        Index(
            "ix_teacher_availability_lookup",
            "teacher_id",
            "term_id",
            "day_of_week",
        ),
    )

    teacher_id: Mapped[UUID] = mapped_column(
        ForeignKey("teacher.id", ondelete="CASCADE"), nullable=False
    )
    term_id: Mapped[UUID] = mapped_column(
        ForeignKey("academic_term.id", ondelete="CASCADE"), nullable=False
    )
    week_start: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    week_end: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    day_of_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    start_slot: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    end_slot: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    availability_type: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)


class Laboratory(AuditMixin, BaseModel):
    __tablename__ = "laboratory"
    __table_args__ = (
        CheckConstraint("safety_capacity >= 1", name="capacity_positive"),
        CheckConstraint(
            "status IN ('ACTIVE', 'LIMITED', 'INACTIVE')",
            name="status_allowed",
        ),
        Index("ix_laboratory_campus_status", "campus_id", "status"),
    )

    lab_code: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    campus_id: Mapped[UUID] = mapped_column(
        ForeignKey("campus.id", ondelete="RESTRICT"), nullable=False
    )
    room_type: Mapped[str | None] = mapped_column(String(64))
    safety_capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    manager_teacher_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("teacher.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ACTIVE"
    )
    description: Mapped[str | None] = mapped_column(Text)


class LabProjectCapability(BaseModel):
    __tablename__ = "lab_project_capability"
    __table_args__ = (
        UniqueConstraint(
            "laboratory_id", "project_id", name="lab_project"
        ),
        CheckConstraint("effective_capacity >= 1", name="capacity_positive"),
        CheckConstraint(
            "status IN ('ACTIVE', 'SUSPENDED', 'INACTIVE')",
            name="status_allowed",
        ),
    )

    laboratory_id: Mapped[UUID] = mapped_column(
        ForeignKey("laboratory.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_project.id", ondelete="CASCADE"),
        nullable=False,
    )
    effective_capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ACTIVE"
    )
    note: Mapped[str | None] = mapped_column(Text)


class EquipmentType(AuditMixin, BaseModel):
    __tablename__ = "equipment_type"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE')",
            name="status_allowed",
        ),
    )

    equipment_code: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str | None] = mapped_column(String(100))
    unit: Mapped[str] = mapped_column(
        String(20), nullable=False, default="台"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ACTIVE"
    )


class LabEquipmentInventory(AuditMixin, BaseModel):
    __tablename__ = "lab_equipment_inventory"
    __table_args__ = (
        UniqueConstraint(
            "laboratory_id",
            "equipment_type_id",
            name="lab_equipment",
        ),
        CheckConstraint("total_quantity >= 0", name="total_nonnegative"),
        CheckConstraint("usable_quantity >= 0", name="usable_nonnegative"),
        CheckConstraint("disabled_quantity >= 0", name="disabled_nonnegative"),
        CheckConstraint(
            "usable_quantity + disabled_quantity <= total_quantity",
            name="quantity_sum_valid",
        ),
    )

    laboratory_id: Mapped[UUID] = mapped_column(
        ForeignKey("laboratory.id", ondelete="CASCADE"), nullable=False
    )
    equipment_type_id: Mapped[UUID] = mapped_column(
        ForeignKey("equipment_type.id", ondelete="RESTRICT"),
        nullable=False,
    )
    total_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    usable_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    disabled_quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class ProjectEquipmentRequirement(BaseModel):
    __tablename__ = "project_equipment_requirement"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "equipment_type_id",
            name="project_equipment",
        ),
        CheckConstraint("units_per_group >= 0", name="units_nonnegative"),
    )

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_project.id", ondelete="CASCADE"),
        nullable=False,
    )
    equipment_type_id: Mapped[UUID] = mapped_column(
        ForeignKey("equipment_type.id", ondelete="RESTRICT"),
        nullable=False,
    )
    units_per_group: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1
    )
    required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    description: Mapped[str | None] = mapped_column(Text)


class ResourceIssueReport(AuditMixin, BaseModel):
    __tablename__ = "resource_issue_report"
    __table_args__ = (
        CheckConstraint(
            "issue_type IN ('EQUIPMENT_FAILURE', 'MATERIAL_SHORTAGE', "
            "'LAB_UNAVAILABLE', 'ENVIRONMENT', 'OTHER')",
            name="issue_type_allowed",
        ),
        CheckConstraint(
            "severity IN ('LOW', 'NORMAL', 'HIGH', 'CRITICAL')",
            name="severity_allowed",
        ),
        CheckConstraint(
            "status IN ('REPORTED', 'PROCESSING', 'RESOLVED', 'CLOSED')",
            name="status_allowed",
        ),
        CheckConstraint("impact_end > impact_start", name="impact_range_valid"),
        Index(
            "ix_resource_issue_time",
            "laboratory_id",
            "impact_start",
            "impact_end",
        ),
    )

    report_no: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    reporter_teacher_id: Mapped[UUID] = mapped_column(
        ForeignKey("teacher.id", ondelete="RESTRICT"), nullable=False
    )
    issue_type: Mapped[str] = mapped_column(String(32), nullable=False)
    laboratory_id: Mapped[UUID] = mapped_column(
        ForeignKey("laboratory.id", ondelete="RESTRICT"), nullable=False
    )
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("experiment_project.id", ondelete="SET NULL")
    )
    equipment_type_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("equipment_type.id", ondelete="SET NULL")
    )
    impact_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    impact_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    severity: Mapped[str] = mapped_column(
        String(20), nullable=False, default="NORMAL"
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="REPORTED"
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    resolution_note: Mapped[str | None] = mapped_column(Text)
