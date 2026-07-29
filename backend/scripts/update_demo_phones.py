"""为演示账号补充手机号，使手机验证码登录可用。"""

import asyncio

from sqlalchemy import update

from app.db.session import AsyncSessionFactory, dispose_database_engine
from app.models.identity import UserAccount


async def main():
    async with AsyncSessionFactory() as session:
        # 管理员
        await session.execute(
            update(UserAccount)
            .where(UserAccount.login_name == "demo_admin")
            .values(phone="13800000001")
        )
        # 教师 demo-t001 ~ demo-t010
        for i in range(1, 11):
            await session.execute(
                update(UserAccount)
                .where(UserAccount.login_name == f"demo-t{i:03d}")
                .values(phone=f"1390000{i:04d}")
            )
        # 前 20 个学生
        for i in range(1, 21):
            await session.execute(
                update(UserAccount)
                .where(UserAccount.login_name == f"d202401{i:04d}")
                .values(phone=f"1500000{i:04d}")
            )

        await session.commit()
        print("已更新演示账号手机号：")
        print("  admin:    13800000001")
        print("  teacher:  13900000001 ~ 13900000010")
        print("  student:  15000000001 ~ 15000000020")

    await dispose_database_engine()


if __name__ == "__main__":
    asyncio.run(main())
