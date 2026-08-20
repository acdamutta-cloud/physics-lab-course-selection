"""安全、幂等地创建系统首个管理员账号。"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from datetime import datetime, timezone

from pwdlib import PasswordHash
from sqlalchemy import select

from app.db.session import AsyncSessionFactory, dispose_database_engine
from app.models.identity import UserAccount

MIN_ADMIN_PASSWORD_LENGTH = 12
MAX_ADMIN_PASSWORD_LENGTH = 128
password_hash = PasswordHash.recommended()


def validate_credentials(login_name: str, password: str) -> tuple[str, str]:
    """校验管理员凭据并返回去除首尾空白后的登录名。"""

    normalized_login = login_name.strip()
    if not normalized_login:
        raise ValueError("管理员登录名不能为空")
    if len(normalized_login) > 64:
        raise ValueError("管理员登录名不能超过 64 个字符")
    if any(character.isspace() for character in normalized_login):
        raise ValueError("管理员登录名不能包含空白字符")
    if len(password) < MIN_ADMIN_PASSWORD_LENGTH:
        raise ValueError(
            f"管理员密码至少需要 {MIN_ADMIN_PASSWORD_LENGTH} 个字符"
        )
    if len(password) > MAX_ADMIN_PASSWORD_LENGTH:
        raise ValueError(
            f"管理员密码不能超过 {MAX_ADMIN_PASSWORD_LENGTH} 个字符"
        )
    if password == normalized_login:
        raise ValueError("管理员密码不能与登录名相同")
    return normalized_login, password


async def create_admin(login_name: str, password: str) -> bool:
    """创建管理员；账号已存在且角色正确时保持不变并返回 False。"""

    normalized_login, validated_password = validate_credentials(
        login_name, password
    )
    async with AsyncSessionFactory() as session:
        existing = await session.scalar(
            select(UserAccount).where(
                UserAccount.login_name == normalized_login
            )
        )
        if existing is not None:
            if existing.user_type != "ADMIN":
                raise ValueError(
                    f"登录名 {normalized_login!r} 已被非管理员账号占用"
                )
            print(f"管理员 {normalized_login!r} 已存在，未修改密码或状态。")
            return False

        session.add(
            UserAccount(
                login_name=normalized_login,
                password_hash=password_hash.hash(validated_password),
                user_type="ADMIN",
                status="ACTIVE",
                password_changed_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
        print(f"管理员 {normalized_login!r} 创建成功。")
        return True


def read_password(*, password_stdin: bool) -> str:
    """从交互终端或标准输入读取密码，不接受明文命令行参数。"""

    if password_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    first = getpass.getpass("管理员密码：")
    second = getpass.getpass("再次输入管理员密码：")
    if first != second:
        raise ValueError("两次输入的管理员密码不一致")
    return first


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="创建系统首个管理员；已存在的管理员不会被覆盖。"
    )
    parser.add_argument(
        "--login-name",
        default="admin",
        help="管理员登录名（默认：admin）",
    )
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="从标准输入读取一行密码，适用于自动化部署",
    )
    return parser


async def async_main() -> int:
    args = build_parser().parse_args()
    try:
        password = read_password(password_stdin=args.password_stdin)
        await create_admin(args.login_name, password)
        return 0
    except (ValueError, EOFError) as error:
        print(f"创建管理员失败：{error}", file=sys.stderr)
        return 2
    finally:
        await dispose_database_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))
