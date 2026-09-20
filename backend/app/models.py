from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class StoreStatus(StrEnum):
    REQUESTED = "requested"
    PROVISIONING = "provisioning"
    INITIALIZING = "initializing"
    READY = "ready"
    FAILED = "failed"
    DELETING = "deleting"
    DELETED = "deleted"


IN_PROGRESS = (StoreStatus.REQUESTED, StoreStatus.PROVISIONING, StoreStatus.INITIALIZING)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    stores: Mapped[list["Store"]] = relationship(back_populates="owner")


class Store(Base):
    __tablename__ = "stores"
    __table_args__ = (
        Index(
            "uq_active_store_namespace",
            "namespace",
            unique=True,
            sqlite_where=text("status != 'deleted'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(30))
    namespace: Mapped[str] = mapped_column(String(63))
    status: Mapped[str] = mapped_column(String(16), default=StoreStatus.REQUESTED, index=True)
    storage_gi: Mapped[int] = mapped_column(default=1, server_default="1")
    products: Mapped[list] = mapped_column(JSON, default=list)
    admin_password_enc: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String(255))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    owner: Mapped[User] = relationship(back_populates="stores")
    events: Mapped[list["StoreEvent"]] = relationship(
        back_populates="store", cascade="all, delete-orphan", order_by="StoreEvent.id"
    )

    @property
    def owner_username(self) -> str:
        return self.owner.username


class StoreEvent(Base):
    __tablename__ = "store_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    level: Mapped[str] = mapped_column(String(8), default="info")
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    store: Mapped[Store] = relationship(back_populates="events")
