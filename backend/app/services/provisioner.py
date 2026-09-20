import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.k8s import manifests as m
from app.k8s.client import get_cluster
from app.k8s.cluster import JOB_FAILED, JOB_SUCCEEDED, Cluster
from app.k8s.wait import wait_until
from app.models import IN_PROGRESS, Store, StoreEvent, StoreStatus
from app.naming import site_url, store_host
from app.security import decrypt_secret
from app.services import executor

logger = logging.getLogger(__name__)

ERROR_LIMIT = 1000


class Cancelled(Exception):
    pass


class ProvisioningError(RuntimeError):
    pass


def build_spec(store: Store, settings: Settings) -> m.StoreSpec:
    password = None
    if store.admin_password_enc:
        password = decrypt_secret(store.admin_password_enc)
        if password is None:
            raise ProvisioningError(
                "The stored admin password can no longer be decrypted (SECRET_KEY changed). "
                "Delete the store and create it again."
            )
    return m.StoreSpec(
        store_id=store.id,
        name=store.name,
        namespace=store.namespace,
        site_url=site_url(store.name, settings),
        host=store_host(store.name, settings),
        admin_email=store.owner.email,
        settings=settings,
        storage_gi=store.storage_gi,
        admin_password=password,
        products=list(store.products or []),
    )


def add_event(db: Session, store: Store, message: str, level: str = "info") -> None:
    db.add(StoreEvent(store_id=store.id, level=level, message=message))


class _Provision:
    def __init__(self, db: Session, store: Store, cluster: Cluster, settings: Settings) -> None:
        self.db = db
        self.store = store
        self.cluster = cluster
        self.settings = settings
        self.spec = build_spec(store, settings)

    def run(self) -> None:
        spec, ns, c, s = self.spec, self.spec.namespace, self.cluster, self.settings

        self._transition(StoreStatus.PROVISIONING, "Provisioning started")
        c.ensure_namespace(m.namespace(spec))
        self._apply("Namespace ready", m.resource_quota(spec), m.limit_range(spec))
        credentials = [
            m.mysql_secret(spec),
            m.admin_secret(spec),
            m.store_config(spec),
            m.scripts_config(spec),
        ]
        if s.razorpay_enabled:
            credentials.append(m.razorpay_secret(spec))
        self._apply("Credentials and configuration created", *credentials)
        self._clear_admin_password()
        self._apply("MySQL deployed", m.mysql_service(spec), m.mysql_statefulset(spec))
        self._wait("MySQL", s.mysql_ready_timeout, lambda: c.statefulset_ready(ns, m.MYSQL))
        self._log("MySQL is ready")

        web = [m.wordpress_pvc(spec), m.wordpress_deployment(spec), m.wordpress_service(spec)]
        if s.ingress_enabled:
            web.append(m.wordpress_ingress(spec))
        self._apply("WordPress deployed", *web)
        self._wait(
            "WordPress", s.wordpress_ready_timeout, lambda: c.deployment_ready(ns, m.WORDPRESS)
        )
        self._log("WordPress is ready")

        self._transition(StoreStatus.INITIALIZING, "Installing WooCommerce")
        self._run_init_job()

        url = site_url(self.store.name, s) if s.ingress_enabled else None
        self._transition(StoreStatus.READY, "Store is ready", url=url, error=None)

    def _run_init_job(self) -> None:
        ns, c = self.spec.namespace, self.cluster
        state = c.job_state(ns, m.INIT_JOB)
        if state == JOB_SUCCEEDED:
            return
        if state == JOB_FAILED:
            c.delete_job(ns, m.INIT_JOB)
            state = None
        if state is None:
            self._checkpoint()
            c.ensure(m.init_job(self.spec))

        def finished() -> bool:
            self._checkpoint()
            current = c.job_state(ns, m.INIT_JOB)
            if current == JOB_FAILED:
                tail = c.job_log_tail(ns, m.INIT_JOB).strip()
                raise ProvisioningError(
                    "WP-CLI initialization failed" + (f":\n{tail}" if tail else "")
                )
            return current == JOB_SUCCEEDED

        wait_until(
            finished,
            timeout=self.settings.init_timeout,
            interval=self.settings.poll_interval_seconds,
            description="WooCommerce initialization",
        )

    def _apply(self, message: str, *resources: dict) -> None:
        self._checkpoint()
        for resource in resources:
            self.cluster.ensure(resource)
        self._log(message)

    def _wait(self, description: str, timeout: int, condition) -> None:
        def ready() -> bool:
            self._checkpoint()
            return condition()

        wait_until(
            ready,
            timeout=timeout,
            interval=self.settings.poll_interval_seconds,
            description=description,
        )

    def _clear_admin_password(self) -> None:
        if self.store.admin_password_enc:
            self.store.admin_password_enc = None
            self.db.commit()

    def _checkpoint(self) -> None:
        self.db.refresh(self.store)
        if self.store.status in (StoreStatus.DELETING, StoreStatus.DELETED):
            raise Cancelled

    def _log(self, message: str) -> None:
        add_event(self.db, self.store, message)
        self.db.commit()

    def _transition(self, status: StoreStatus, message: str, **fields) -> None:
        self._checkpoint()
        for key, value in fields.items():
            setattr(self.store, key, value)
        self.store.status = status
        add_event(self.db, self.store, message)
        self.db.commit()


def provision_store(store_id: int) -> None:
    with SessionLocal() as db:
        store = db.get(Store, store_id)
        if store is None or store.status not in IN_PROGRESS:
            return
        try:
            _Provision(db, store, get_cluster(), get_settings()).run()
        except Cancelled:
            logger.info("Provisioning of store %s cancelled", store_id)
        except Exception as exc:
            logger.exception("Provisioning of store %s failed", store_id)
            _mark_failed(db, store, str(exc), unless_deleting=True)


def delete_store(store_id: int) -> None:
    settings = get_settings()
    with SessionLocal() as db:
        store = db.get(Store, store_id)
        if store is None or store.status == StoreStatus.DELETED:
            return
        try:
            cluster = get_cluster()
            cluster.delete_namespace(store.namespace)
            wait_until(
                lambda: not cluster.namespace_exists(store.namespace),
                timeout=settings.namespace_delete_timeout,
                interval=settings.poll_interval_seconds,
                description=f"namespace {store.namespace} to be removed",
            )
            store.status = StoreStatus.DELETED
            store.url = None
            store.error = None
            add_event(db, store, "Store deleted")
            db.commit()
        except Exception as exc:
            logger.exception("Deletion of store %s failed", store_id)
            _mark_failed(db, store, f"Deletion failed: {exc}")


def resume_pending() -> None:
    with SessionLocal() as db:
        rows = db.execute(
            select(Store.id, Store.status).where(
                Store.status.in_([*IN_PROGRESS, StoreStatus.DELETING])
            )
        ).all()
    for store_id, status in rows:
        worker = delete_store if status == StoreStatus.DELETING else provision_store
        executor.submit(worker, store_id)


def _mark_failed(
    db: Session, store: Store, message: str, *, unless_deleting: bool = False
) -> None:
    db.rollback()
    db.refresh(store)
    if unless_deleting and store.status in (StoreStatus.DELETING, StoreStatus.DELETED):
        return
    store.status = StoreStatus.FAILED
    store.error = message[:ERROR_LIMIT]
    add_event(db, store, message[:ERROR_LIMIT], level="error")
    db.commit()
