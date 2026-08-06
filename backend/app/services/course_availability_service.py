import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.curriculum import AcademicTerm, ExperimentCourse
from app.models.identity import Student, StudentBusyBitmap
from app.models.scheduling import (
    CourseTimeAvailability,
    TeachingTask,
    TeachingTaskCohort,
)

RATIO_QUANTUM = Decimal("0.000001")
COURSE_AVAILABILITY_MAPPING_VERSION = 1


@dataclass(frozen=True)
class AvailabilityRefreshResult:
    course_id: UUID
    term_id: UUID
    calculation_batch_id: UUID
    calculation_version: int
    source_hash: str
    target_student_count: int
    known_student_count: int
    unknown_student_count: int
    row_count: int


@dataclass(frozen=True)
class ApproximateBlockAvailability:
    free_student_count: int
    free_ratio: Decimal
    data_coverage_ratio: Decimal
    assessment: str = "APPROXIMATE"


def bitmap_slot_is_busy(
    bitmap: bytes,
    *,
    start_week: int,
    days_per_week: int,
    slots_per_day: int,
    week_no: int,
    day_of_week: int,
    slot_no: int,
) -> bool | None:
    """Read one slot; return None when the requested bit is unavailable."""

    relative_week = week_no - start_week
    if (
        relative_week < 0
        or day_of_week < 1
        or day_of_week > days_per_week
        or slot_no < 1
        or slot_no > slots_per_day
    ):
        return None
    index = (
        relative_week * days_per_week * slots_per_day
        + (day_of_week - 1) * slots_per_day
        + slot_no
        - 1
    )
    if index < 0 or index // 8 >= len(bitmap):
        return None
    return bool(bitmap[index // 8] & (1 << (7 - (index % 8))))


def _ratio(numerator: int, denominator: int) -> Decimal:
    if denominator == 0:
        return Decimal(0).quantize(RATIO_QUANTUM)
    return (Decimal(numerator) / Decimal(denominator)).quantize(
        RATIO_QUANTUM
    )


def _source_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _compatible_bitmap(
    bitmap: StudentBusyBitmap,
    term: AcademicTerm,
) -> bool:
    expected_bits = (
        term.total_weeks * term.days_per_week * term.slots_per_day
    )
    return (
        bitmap.start_week == 1
        and bitmap.end_week >= term.total_weeks
        and bitmap.days_per_week == term.days_per_week
        and bitmap.slots_per_day == term.slots_per_day
        and len(bitmap.bitmap) >= math.ceil(expected_bits / 8)
    )


def estimate_block_availability(
    rows: list[CourseTimeAvailability],
) -> ApproximateBlockAvailability:
    """Use the minimum single-slot availability as a coarse block score."""

    if not rows:
        raise ValueError("连续实验时段至少需要一条单节统计")
    return ApproximateBlockAvailability(
        free_student_count=min(row.free_student_count for row in rows),
        free_ratio=min(row.free_ratio for row in rows),
        data_coverage_ratio=min(
            row.data_coverage_ratio for row in rows
        ),
    )


async def _course_target_student_ids(
    session: AsyncSession,
    *,
    course_id: UUID,
    term_id: UUID,
) -> list[UUID]:
    return list(
        (
            await session.execute(
                select(Student.id)
                .join(
                    TeachingTaskCohort,
                    and_(
                        Student.major_id
                        == TeachingTaskCohort.major_id,
                        Student.enrollment_year
                        == TeachingTaskCohort.enrollment_year,
                        or_(
                            TeachingTaskCohort.class_id.is_(None),
                            Student.class_id
                            == TeachingTaskCohort.class_id,
                        ),
                    ),
                )
                .join(
                    TeachingTask,
                    TeachingTask.id == TeachingTaskCohort.task_id,
                )
                .where(
                    TeachingTask.course_id == course_id,
                    TeachingTask.term_id == term_id,
                    Student.academic_status == "ACTIVE",
                )
                .distinct()
                .order_by(Student.id)
            )
        ).scalars()
    )


async def _cohort_source_rows(
    session: AsyncSession,
    *,
    course_id: UUID,
    term_id: UUID,
) -> list[tuple[UUID, UUID, UUID, int, UUID | None]]:
    return list(
        (
            await session.execute(
                select(
                    TeachingTask.id,
                    TeachingTaskCohort.id,
                    TeachingTaskCohort.major_id,
                    TeachingTaskCohort.enrollment_year,
                    TeachingTaskCohort.class_id,
                )
                .join(
                    TeachingTaskCohort,
                    TeachingTaskCohort.task_id == TeachingTask.id,
                )
                .where(
                    TeachingTask.course_id == course_id,
                    TeachingTask.term_id == term_id,
                )
                .order_by(
                    TeachingTask.id,
                    TeachingTaskCohort.id,
                )
            )
        ).all()
    )


async def refresh_course_time_availability(
    session: AsyncSession,
    course_id: UUID,
    term_id: UUID,
) -> AvailabilityRefreshResult:
    """Replace the current course-level, single-slot availability grid."""

    course = await session.get(ExperimentCourse, course_id)
    if course is None:
        raise ValueError("实验课程不存在")
    term = await session.get(AcademicTerm, term_id)
    if term is None:
        raise ValueError("学期不存在")

    student_ids = await _course_target_student_ids(
        session,
        course_id=course_id,
        term_id=term_id,
    )
    cohort_rows = await _cohort_source_rows(
        session,
        course_id=course_id,
        term_id=term_id,
    )

    bitmap_rows: list[StudentBusyBitmap] = []
    if student_ids:
        bitmap_rows = list(
            (
                await session.execute(
                    select(StudentBusyBitmap)
                    .where(
                        StudentBusyBitmap.student_id.in_(student_ids),
                        StudentBusyBitmap.term_id == term_id,
                    )
                    .order_by(
                        StudentBusyBitmap.student_id,
                        StudentBusyBitmap.mapping_version.desc(),
                    )
                )
            )
            .scalars()
            .all()
        )

    compatible_by_student: dict[UUID, StudentBusyBitmap] = {}
    for bitmap in bitmap_rows:
        if (
            bitmap.student_id not in compatible_by_student
            and _compatible_bitmap(bitmap, term)
        ):
            compatible_by_student[bitmap.student_id] = bitmap

    target_count = len(student_ids)
    known_count = len(compatible_by_student)
    unknown_count = target_count - known_count
    calculation_batch_id = uuid4()
    calculated_at = datetime.now(UTC)
    current_version = await session.scalar(
        select(func.max(CourseTimeAvailability.calculation_version)).where(
            CourseTimeAvailability.course_id == course_id,
            CourseTimeAvailability.term_id == term_id,
        )
    )
    calculation_version = int(current_version or 0) + 1
    source_hash = _source_hash(
        {
            "course_id": course_id,
            "term_id": term_id,
            "term_grid": {
                "total_weeks": term.total_weeks,
                "days_per_week": term.days_per_week,
                "slots_per_day": term.slots_per_day,
            },
            "cohorts": cohort_rows,
            "student_ids": student_ids,
            "bitmaps": [
                {
                    "student_id": student_id,
                    "mapping_version": bitmap.mapping_version,
                    "source_version": bitmap.source_version,
                    "bitmap_hash": sha256(bitmap.bitmap).hexdigest(),
                }
                for student_id, bitmap in sorted(
                    compatible_by_student.items(),
                    key=lambda item: str(item[0]),
                )
            ],
        }
    )

    await session.execute(
        delete(CourseTimeAvailability).where(
            CourseTimeAvailability.course_id == course_id,
            CourseTimeAvailability.term_id == term_id,
        )
    )

    coverage_ratio = _ratio(known_count, target_count)
    records: list[CourseTimeAvailability] = []
    for week_no in range(1, term.total_weeks + 1):
        for day_of_week in range(1, term.days_per_week + 1):
            for slot_no in range(1, term.slots_per_day + 1):
                busy_count = sum(
                    bool(
                        bitmap_slot_is_busy(
                            bitmap.bitmap,
                            start_week=bitmap.start_week,
                            days_per_week=bitmap.days_per_week,
                            slots_per_day=bitmap.slots_per_day,
                            week_no=week_no,
                            day_of_week=day_of_week,
                            slot_no=slot_no,
                        )
                    )
                    for bitmap in compatible_by_student.values()
                )
                free_count = known_count - busy_count
                records.append(
                    CourseTimeAvailability(
                        course_id=course_id,
                        term_id=term_id,
                        week_no=week_no,
                        day_of_week=day_of_week,
                        slot_no=slot_no,
                        target_student_count=target_count,
                        known_student_count=known_count,
                        free_student_count=free_count,
                        busy_student_count=busy_count,
                        unknown_student_count=unknown_count,
                        free_ratio=_ratio(free_count, target_count),
                        data_coverage_ratio=coverage_ratio,
                        mapping_version=(
                            COURSE_AVAILABILITY_MAPPING_VERSION
                        ),
                        calculation_version=calculation_version,
                        calculation_batch_id=calculation_batch_id,
                        source_hash=source_hash,
                        calculated_at=calculated_at,
                    )
                )

    session.add_all(records)
    await session.flush()
    return AvailabilityRefreshResult(
        course_id=course_id,
        term_id=term_id,
        calculation_batch_id=calculation_batch_id,
        calculation_version=calculation_version,
        source_hash=source_hash,
        target_student_count=target_count,
        known_student_count=known_count,
        unknown_student_count=unknown_count,
        row_count=len(records),
    )


async def get_course_time_availability(
    session: AsyncSession,
    course_id: UUID,
    term_id: UUID,
) -> list[CourseTimeAvailability]:
    return list(
        (
            await session.execute(
                select(CourseTimeAvailability)
                .where(
                    CourseTimeAvailability.course_id == course_id,
                    CourseTimeAvailability.term_id == term_id,
                )
                .order_by(
                    CourseTimeAvailability.week_no,
                    CourseTimeAvailability.day_of_week,
                    CourseTimeAvailability.slot_no,
                )
            )
        )
        .scalars()
        .all()
    )
