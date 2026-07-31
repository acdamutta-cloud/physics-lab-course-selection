from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AuditMixin, BaseModel


class Campus(AuditMixin, BaseModel):
    __tablename__ = "campus"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE')",
            name="status_allowed",
        ),
    )

    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    address: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ACTIVE"
    )


class Major(AuditMixin, BaseModel):
    __tablename__ = "major"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE')",
            name="status_allowed",
        ),
    )

    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    degree_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ENGINEERING"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ACTIVE"
    )


class StudentClass(AuditMixin, BaseModel):
    __tablename__ = "student_class"
    __table_args__ = (
        UniqueConstraint(
            "major_id",
            "enrollment_year",
            "code",
            name="major_year_code",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'GRADUATED', 'INACTIVE')",
            name="status_allowed",
        ),
        Index("ix_student_class_major_year", "major_id", "enrollment_year"),
    )

    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    major_id: Mapped[UUID] = mapped_column(
        ForeignKey("major.id", ondelete="RESTRICT"), nullable=False
    )
    enrollment_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    campus_id: Mapped[UUID] = mapped_column(
        ForeignKey("campus.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ACTIVE"
    )


class UserAccount(AuditMixin, BaseModel):
    __tablename__ = "user_account"
    __table_args__ = (
        CheckConstraint(
            "user_type IN ('STUDENT', 'TEACHER', 'ADMIN', 'AUDITOR')",
            name="user_type_allowed",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'LOCKED', 'DISABLED')",
            name="status_allowed",
        ),
        Index("ix_user_account_type_status", "user_type", "status"),
    )

    login_name: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    user_type: Mapped[str] = mapped_column(String(20), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ACTIVE"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class Student(AuditMixin, BaseModel):
    __tablename__ = "student"
    __table_args__ = (
        CheckConstraint(
            "academic_status IN "
            "('ACTIVE', 'SUSPENDED', 'GRADUATED', 'WITHDRAWN')",
            name="academic_status_allowed",
        ),
        Index("ix_student_major_year", "major_id", "enrollment_year"),
        Index("ix_student_class", "class_id"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    student_no: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    gender: Mapped[str | None] = mapped_column(String(16))
    birth_date: Mapped[date | None] = mapped_column(Date)
    enrollment_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    major_id: Mapped[UUID] = mapped_column(
        ForeignKey("major.id", ondelete="RESTRICT"), nullable=False
    )
    class_id: Mapped[UUID] = mapped_column(
        ForeignKey("student_class.id", ondelete="RESTRICT"), nullable=False
    )
    campus_id: Mapped[UUID] = mapped_column(
        ForeignKey("campus.id", ondelete="RESTRICT"), nullable=False
    )
    academic_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ACTIVE"
    )


class Teacher(AuditMixin, BaseModel):
    __tablename__ = "teacher"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'LEAVE', 'INACTIVE')",
            name="status_allowed",
        ),
        Index("ix_teacher_campus_status", "campus_id", "status"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    employee_no: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    campus_id: Mapped[UUID] = mapped_column(
        ForeignKey("campus.id", ondelete="RESTRICT"), nullable=False
    )
    department: Mapped[str | None] = mapped_column(String(100))
    title: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ACTIVE"
    )


class StudentBusyBitmap(BaseModel):
    __tablename__ = "student_busy_bitmap"
    __table_args__ = (
        UniqueConstraint(
            "student_id",
            "term_id",
            "mapping_version",
            name="student_term_mapping",
        ),
        CheckConstraint("start_week >= 1", name="start_week_positive"),
        CheckConstraint("end_week >= start_week", name="week_range_valid"),
        CheckConstraint("days_per_week BETWEEN 1 AND 7", name="days_valid"),
        CheckConstraint("slots_per_day >= 1", name="slots_positive"),
    )

    student_id: Mapped[UUID] = mapped_column(
        ForeignKey("student.id", ondelete="CASCADE"), nullable=False
    )
    term_id: Mapped[UUID] = mapped_column(
        ForeignKey("academic_term.id", ondelete="CASCADE"), nullable=False
    )
    start_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    end_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    days_per_week: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=7
    )
    slots_per_day: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=12
    )
    bitmap: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    mapping_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    source_version: Mapped[str | None] = mapped_column(String(64))


class TeacherBusyBitmap(BaseModel):
    """教师忙闲位图。与 StudentBusyBitmap 结构对称，初始全空。
    实验场次为 4 节连排，排入后批量更新位图。"""
    __tablename__ = "teacher_busy_bitmap"
    __table_args__ = (
        UniqueConstraint(
            "teacher_id",
            "term_id",
            "mapping_version",
            name="teacher_term_mapping",
        ),
        CheckConstraint("start_week >= 1", name="tb_start_week_positive"),
        CheckConstraint("end_week >= start_week", name="tb_week_range_valid"),
        CheckConstraint("days_per_week BETWEEN 1 AND 7", name="tb_days_valid"),
        CheckConstraint("slots_per_day >= 1", name="tb_slots_positive"),
    )

    teacher_id: Mapped[UUID] = mapped_column(
        ForeignKey("teacher.id", ondelete="CASCADE"), nullable=False
    )
    term_id: Mapped[UUID] = mapped_column(
        ForeignKey("academic_term.id", ondelete="CASCADE"), nullable=False
    )
    start_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    end_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    days_per_week: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=7
    )
    slots_per_day: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=12
    )
    bitmap: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    mapping_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    source_version: Mapped[str | None] = mapped_column(String(64))
