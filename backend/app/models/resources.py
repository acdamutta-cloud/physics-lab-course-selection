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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

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

    equipment_inventory: Mapped[list["LabEquipmentInventory"]] = relationship(
        "LabEquipmentInventory", viewonly=True,
    )


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
        CheckConstraint(
            "students_per_unit IS NULL OR students_per_unit >= 1",
            name="students_per_unit_positive",
        ),
        CheckConstraint(
            "sharing_rule_status IN ('UNPARSED', 'CONFIRMED', 'AMBIGUOUS')",
            name="sharing_rule_status_allowed",
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
    usage_note: Mapped[str | None] = mapped_column(Text)
    students_per_unit: Mapped[int | None] = mapped_column(SmallInteger)
    sharing_rule_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="UNPARSED"
    )
    sharing_rule_source: Mapped[str | None] = mapped_column(String(20))
    sharing_rule_evidence: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    equipment_type: Mapped["EquipmentType"] = relationship("EquipmentType", viewonly=True)


class EquipmentAsset(AuditMixin, BaseModel):
    """A single, permanently identified physical instrument."""

    __tablename__ = "equipment_asset"
    __table_args__ = (
        CheckConstraint(
            "status IN ('AVAILABLE', 'QUARANTINED', 'UNDER_REPAIR', "
            "'DISABLED', 'LOST', 'SCRAPPED')",
            name="status_allowed",
        ),
        Index("ix_equipment_asset_inventory_status", "current_inventory_id", "status"),
    )

    instrument_no: Mapped[str] = mapped_column(String(96), nullable=False, unique=True)
    equipment_type_id: Mapped[UUID] = mapped_column(
        ForeignKey("equipment_type.id", ondelete="RESTRICT"), nullable=False
    )
    current_inventory_id: Mapped[UUID] = mapped_column(
        ForeignKey("lab_equipment_inventory.id", ondelete="RESTRICT"), nullable=False
    )
    origin_laboratory_id: Mapped[UUID] = mapped_column(
        ForeignKey("laboratory.id", ondelete="RESTRICT"), nullable=False
    )
    purchase_date: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="AVAILABLE")


class EquipmentAssetEvent(AuditMixin, BaseModel):
    __tablename__ = "equipment_asset_event"
    __table_args__ = (Index("ix_equipment_asset_event_asset_time", "asset_id", "created_at"),)

    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("equipment_asset.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str | None] = mapped_column(String(24))
    from_inventory_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("lab_equipment_inventory.id", ondelete="SET NULL")
    )
    to_inventory_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("lab_equipment_inventory.id", ondelete="SET NULL")
    )
    resource_issue_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("resource_issue_report.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(Text)


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
            "'LAB_UNAVAILABLE', 'ENVIRONMENT', 'EQUIPMENT_SCRAP', 'OTHER')",
            name="issue_type_allowed",
        ),
        CheckConstraint(
            "severity IN ('LOW', 'NORMAL', 'HIGH', 'CRITICAL')",
            name="severity_allowed",
        ),
        CheckConstraint(
            "status IN ('REPORTED', 'PENDING_REVIEW', 'PROCESSING', "
            "'RESOLVED', 'REJECTED', 'CLOSED', 'SCRAP_REVIEW', 'RELOCATION_REQUIRED', "
            "'READY_TO_EXECUTE', 'SCRAPPED')",
            name="status_allowed",
        ),
        CheckConstraint("impact_end > impact_start", name="impact_range_valid"),
        CheckConstraint(
            "affected_quantity > 0 AND approved_quantity >= 0 AND "
            "restored_quantity >= 0 AND restored_quantity <= approved_quantity",
            name="resource_issue_quantities_valid",
        ),
        CheckConstraint(
            "remediation_status IN ('NOT_REQUIRED', 'REMEDIATION_REQUIRED', "
            "'PARTIALLY_REMEDIATED', 'REMEDIATED')",
            name="resource_remediation_status_allowed",
        ),
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
    inventory_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("lab_equipment_inventory.id", ondelete="RESTRICT")
    )
    affected_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    approved_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    restored_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
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
    remediation_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="NOT_REQUIRED"
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    approved_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    source_issue_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("resource_issue_report.id", ondelete="SET NULL")
    )


class ResourceIssueAsset(AuditMixin, BaseModel):
    __tablename__ = "resource_issue_asset"
    __table_args__ = (
        UniqueConstraint(
            "resource_issue_id", "asset_id",
            name="uq_resource_issue_asset_issue_asset",
        ),
        Index(
            "uq_resource_issue_asset_active",
            "asset_id",
            unique=True,
            postgresql_where=text("active = true"),
        ),
    )

    resource_issue_id: Mapped[UUID] = mapped_column(
        ForeignKey("resource_issue_report.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("equipment_asset.id", ondelete="RESTRICT"), nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    previous_status: Mapped[str] = mapped_column(String(24), nullable=False)


class ResourceIssueObservation(AuditMixin, BaseModel):
    __tablename__ = "resource_issue_observation"
    __table_args__ = (Index("ix_resource_issue_observation_issue_time", "resource_issue_id", "created_at"),)

    resource_issue_id: Mapped[UUID] = mapped_column(
        ForeignKey("resource_issue_report.id", ondelete="CASCADE"), nullable=False
    )
    reporter_teacher_id: Mapped[UUID] = mapped_column(
        ForeignKey("teacher.id", ondelete="RESTRICT"), nullable=False
    )
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
