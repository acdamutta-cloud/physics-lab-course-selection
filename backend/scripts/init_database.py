from psycopg import connect, sql
from psycopg.conninfo import make_conninfo

from app.core.config.settings import get_settings


def ensure_database_exists() -> bool:
    """在 PostgreSQL 实例中创建目标数据库；已存在时不做修改。"""

    settings = get_settings()
    settings.validate_runtime_secrets()
    maintenance_conninfo = make_conninfo(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        dbname="postgres",
        sslmode=settings.postgres_sslmode,
    )

    with connect(maintenance_conninfo, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (settings.postgres_db,),
            )
            if cursor.fetchone():
                print(f"数据库 {settings.postgres_db} 已存在，未做修改。")
                return False

            cursor.execute(
                sql.SQL("CREATE DATABASE {}").format(
                    sql.Identifier(settings.postgres_db)
                )
            )
            print(f"数据库 {settings.postgres_db} 创建成功。")
            return True


if __name__ == "__main__":
    ensure_database_exists()
