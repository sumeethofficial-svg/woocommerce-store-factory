from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.deps import CurrentUser, DbSession
from app.k8s import manifests as m
from app.k8s.client import get_cluster
from app.models import Store, StoreEvent, StoreStatus, User
from app.naming import namespace_for
from app.schemas import (
    AdminCredentials,
    Quota,
    StoreCreate,
    StoreEventOut,
    StoreOut,
    UsageOut,
)
from app.security import encrypt_secret
from app.services import executor, provisioner

router = APIRouter(prefix="/api/stores", tags=["stores"])

EVENT_LIMIT = 200


def _owned_store(db: DbSession, user: User, store_id: int) -> Store:
    store = db.get(Store, store_id)
    if store is None or store.owner_id != user.id or store.status == StoreStatus.DELETED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Store not found")
    return store


def _usage(db: DbSession, user: User) -> tuple[int, int]:
    count, storage = db.execute(
        select(func.count(Store.id), func.coalesce(func.sum(Store.storage_gi), 0)).where(
            Store.owner_id == user.id, Store.status != StoreStatus.DELETED
        )
    ).one()
    return count, storage


@router.get("", response_model=list[StoreOut])
def list_stores(db: DbSession, user: CurrentUser) -> list[Store]:
    query = (
        select(Store)
        .options(selectinload(Store.owner))
        .where(Store.owner_id == user.id, Store.status != StoreStatus.DELETED)
        .order_by(Store.id.desc())
    )
    return list(db.scalars(query))


@router.get("/usage", response_model=UsageOut)
def usage(db: DbSession, user: CurrentUser) -> UsageOut:
    settings = get_settings()
    count, storage = _usage(db, user)
    return UsageOut(
        stores=Quota(used=count, limit=settings.max_stores_per_user),
        storage_gi=Quota(used=storage, limit=settings.max_storage_gi_per_user),
        max_store_storage_gi=settings.max_store_storage_gi,
        mysql_storage_gi=settings.mysql_storage_gi,
        online_payments=settings.razorpay_enabled,
    )


@router.post("", response_model=StoreOut, status_code=status.HTTP_201_CREATED)
def create_store(payload: StoreCreate, db: DbSession, user: CurrentUser) -> Store:
    settings = get_settings()
    if payload.storage_gi > settings.max_store_storage_gi:
        raise HTTPException(
            422,
            f"A store can use at most {settings.max_store_storage_gi} Gi of WordPress storage",
        )

    password = payload.admin_password.get_secret_value() if payload.admin_password else None
    store = Store(
        owner_id=user.id,
        name=payload.name,
        namespace=namespace_for(payload.name),
        storage_gi=payload.storage_gi,
        products=[product.as_record() for product in payload.products],
        admin_password_enc=encrypt_secret(password) if password else None,
    )
    db.add(store)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Store name is already taken") from None

    count, storage = _usage(db, user)
    if count > settings.max_stores_per_user:
        db.rollback()
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"Store limit reached ({settings.max_stores_per_user})"
        )
    if storage > settings.max_storage_gi_per_user:
        db.rollback()
        used = storage - payload.storage_gi
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Storage quota exceeded: {used} of {settings.max_storage_gi_per_user} Gi in use, "
            f"this store needs {payload.storage_gi} Gi",
        )

    provisioner.add_event(db, store, "Store requested")
    db.commit()
    db.refresh(store)

    executor.submit(provisioner.provision_store, store.id)
    return store


@router.get("/{store_id}", response_model=StoreOut)
def get_store(store_id: int, db: DbSession, user: CurrentUser) -> Store:
    return _owned_store(db, user, store_id)


@router.get("/{store_id}/events", response_model=list[StoreEventOut])
def store_events(store_id: int, db: DbSession, user: CurrentUser) -> list[StoreEvent]:
    store = _owned_store(db, user, store_id)
    query = (
        select(StoreEvent)
        .where(StoreEvent.store_id == store.id)
        .order_by(StoreEvent.id.desc())
        .limit(EVENT_LIMIT)
    )
    return list(reversed(db.scalars(query).all()))


@router.get("/{store_id}/credentials", response_model=AdminCredentials)
def admin_credentials(store_id: int, db: DbSession, user: CurrentUser) -> AdminCredentials:
    store = _owned_store(db, user, store_id)
    if store.status != StoreStatus.READY:
        raise HTTPException(status.HTTP_409_CONFLICT, "Store is not ready yet")
    secret = get_cluster().read_secret(store.namespace, m.ADMIN_SECRET)
    if secret is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Admin credentials not found")
    return AdminCredentials(
        username=secret["ADMIN_USER"],
        password=secret["ADMIN_PASSWORD"],
        admin_url=f"{store.url}/wp-admin/" if store.url else None,
    )


@router.post("/{store_id}/retry", response_model=StoreOut, status_code=status.HTTP_202_ACCEPTED)
def retry_store(store_id: int, db: DbSession, user: CurrentUser) -> Store:
    store = _owned_store(db, user, store_id)
    if store.status != StoreStatus.FAILED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only failed stores can be retried")
    store.status = StoreStatus.REQUESTED
    store.error = None
    provisioner.add_event(db, store, "Retry requested")
    db.commit()
    db.refresh(store)
    executor.submit(provisioner.provision_store, store.id)
    return store


@router.delete("/{store_id}", response_model=StoreOut, status_code=status.HTTP_202_ACCEPTED)
def delete_store(store_id: int, db: DbSession, user: CurrentUser) -> Store:
    store = _owned_store(db, user, store_id)
    if store.status == StoreStatus.DELETING:
        return store
    store.status = StoreStatus.DELETING
    store.error = None
    provisioner.add_event(db, store, "Deletion requested")
    db.commit()
    db.refresh(store)
    executor.submit(provisioner.delete_store, store.id)
    return store
