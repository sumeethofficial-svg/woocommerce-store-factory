import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

_pool: ThreadPoolExecutor | None = None


def start(workers: int) -> None:
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="store-worker")


def submit(fn: Callable[..., None], *args) -> None:
    if _pool is None:
        raise RuntimeError("Worker pool is not running")
    _pool.submit(_guard, fn, *args)


def shutdown() -> None:
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=True)
        _pool = None


def _guard(fn: Callable[..., None], *args) -> None:
    try:
        fn(*args)
    except Exception:
        logger.exception("Unhandled error in %s", fn.__name__)
