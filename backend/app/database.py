from collections.abc import Iterator

import re

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()
is_sqlite = settings.database_url.startswith("sqlite")

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False, "timeout": 30} if is_sqlite else {},
)

if is_sqlite:

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


def _username_from_email(email: str, taken: set[str], user_id: int) -> str:
    base = re.sub(r"[^a-z0-9_]", "_", email.split("@")[0].lower())[:24].ljust(3, "_")
    name = base if base not in taken else f"{base}_{user_id}"
    taken.add(name)
    return name


def migrate(target: Engine | None = None) -> None:
    target = target or engine
    inspector = inspect(target)
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    store_columns = {column["name"] for column in inspector.get_columns("stores")}
    with target.begin() as conn:
        if "username" not in user_columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN username VARCHAR(32)"))
            taken: set[str] = set()
            for user_id, email in conn.execute(text("SELECT id, email FROM users")).all():
                conn.execute(
                    text("UPDATE users SET username = :name WHERE id = :id"),
                    {"name": _username_from_email(email, taken, user_id), "id": user_id},
                )
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users (username)"))
        if "storage_gi" not in store_columns:
            conn.execute(text("ALTER TABLE stores ADD COLUMN storage_gi INTEGER NOT NULL DEFAULT 1"))
        if "products" not in store_columns:
            conn.execute(text("ALTER TABLE stores ADD COLUMN products JSON NOT NULL DEFAULT '[]'"))
        if "admin_password_enc" not in store_columns:
            conn.execute(text("ALTER TABLE stores ADD COLUMN admin_password_enc TEXT"))
