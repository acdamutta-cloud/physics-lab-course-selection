"""添加 2026 级演示学生。"""
import asyncio, random
from uuid import uuid4
from pwdlib import PasswordHash
from sqlalchemy import select
from app.db.session import AsyncSessionFactory, dispose_database_engine
from app.models.identity import Student, Major, StudentClass, UserAccount

SURNAMES = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
GIVEN_NAMES = "伟芳强敏杰洋静磊颖昊宇婷鑫浩然博文思雨晨旭丽华明军涛"


async def main():
    async with AsyncSessionFactory() as session:
        majors = (await session.execute(
            select(Major).where(Major.status == "ACTIVE")
        )).scalars().all()
        classes = (await session.execute(
            select(StudentClass).where(StudentClass.enrollment_year == 2024)
        )).scalars().all()
        if not classes:
            print("No classes found")
            return

        pwd_hash = PasswordHash.recommended().hash("Demo@123456")
        rng = random.Random(20260729)
        added = 0

        for major in majors:
            major_classes = [c for c in classes if c.major_id == major.id]
            class_obj = major_classes[0] if major_classes else classes[0]
            major_code = major.code.split("-")[1][:2] if "-" in major.code else "01"

            for local_idx in range(1, 81):
                student_no = f"D2026{major_code}{local_idx:04d}"
                existing = await session.scalar(
                    select(Student).where(Student.student_no == student_no)
                )
                if existing:
                    continue

                account_id = uuid4()
                session.add(UserAccount(
                    id=account_id,
                    login_name=student_no.lower(),
                    password_hash=pwd_hash,
                    user_type="STUDENT",
                    status="ACTIVE",
                ))
                await session.flush()

                session.add(Student(
                    id=uuid4(),
                    user_id=account_id,
                    student_no=student_no,
                    name=rng.choice(SURNAMES) + rng.choice(GIVEN_NAMES),
                    enrollment_year=2026,
                    major_id=major.id,
                    class_id=class_obj.id,
                    campus_id=class_obj.campus_id,
                    academic_status="ACTIVE",
                ))
                added += 1

        await session.commit()
        print(f"Added {added} students (enrollment_year=2026)")

    await dispose_database_engine()


if __name__ == "__main__":
    asyncio.run(main())
