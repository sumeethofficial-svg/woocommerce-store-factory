import os
import tempfile

_workdir = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_workdir}/test.db"
os.environ["SECRET_KEY"] = "test-secret-key-that-is-long-enough-for-hs256"
os.environ["BCRYPT_ROUNDS"] = "4"
os.environ["POLL_INTERVAL_SECONDS"] = "0"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.k8s.cluster import Cluster  # noqa: E402
from app.main import app  # noqa: E402
from app.services import executor, provisioner  # noqa: E402
from tests.fake_k8s import FakeK8s  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


@pytest.fixture(autouse=True)
def inline_executor(monkeypatch):
    monkeypatch.setattr(executor, "submit", lambda fn, *args: fn(*args))


@pytest.fixture
def k8s(monkeypatch) -> FakeK8s:
    fake = FakeK8s()
    cluster = Cluster(core=fake, apps=fake, batch=fake, networking=fake)
    monkeypatch.setattr(provisioner, "get_cluster", lambda: cluster)
    monkeypatch.setattr("app.api.stores.get_cluster", lambda: cluster)
    return fake


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def register_and_login(client: TestClient, username: str = "owner") -> dict[str, str]:
    client.post(
        "/api/auth/register",
        json={"username": username, "email": f"{username}@example.com", "password": "correct-horse"},
    )
    response = client.post(
        "/api/auth/login", data={"username": username, "password": "correct-horse"}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def auth(client) -> dict[str, str]:
    return register_and_login(client)
