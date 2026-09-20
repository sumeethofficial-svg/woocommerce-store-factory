import time
from collections.abc import Callable


class WaitTimeout(RuntimeError):
    pass


def wait_until(
    condition: Callable[[], bool], *, timeout: float, interval: float, description: str
) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            raise WaitTimeout(f"Timed out after {timeout:.0f}s waiting for {description}")
        time.sleep(interval)
