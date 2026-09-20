from tests.conftest import register_and_login


def test_health(client):
    assert client.get("/health").json() == {"status": "healthy"}


def test_register_login_and_me(client):
    headers = register_and_login(client, "sam")
    response = client.get("/api/auth/me", headers=headers)
    assert response.status_code == 200
    assert response.json()["username"] == "sam"
    assert response.json()["email"] == "sam@example.com"


def test_duplicate_registration_conflicts(client):
    payload = {"username": "dup", "email": "dup@example.com", "password": "correct-horse"}
    assert client.post("/api/auth/register", json=payload).status_code == 201
    assert client.post("/api/auth/register", json=payload).status_code == 409


def test_weak_password_rejected(client):
    response = client.post(
        "/api/auth/register",
        json={"username": "weak", "email": "weak@example.com", "password": "short"},
    )
    assert response.status_code == 422


def test_wrong_password_rejected(client):
    register_and_login(client, "who")
    response = client.post(
        "/api/auth/login", data={"username": "who", "password": "wrong-password"}
    )
    assert response.status_code == 401


def test_unknown_user_rejected(client):
    response = client.post(
        "/api/auth/login", data={"username": "ghost", "password": "correct-horse"}
    )
    assert response.status_code == 401


def test_protected_routes_require_valid_token(client):
    assert client.get("/api/stores").status_code == 401
    bad = {"Authorization": "Bearer not-a-token"}
    assert client.get("/api/stores", headers=bad).status_code == 401


def test_login_accepts_email_as_well_as_username(client):
    register_and_login(client, "mail")
    response = client.post(
        "/api/auth/login", data={"username": "Mail@Example.com", "password": "correct-horse"}
    )
    assert response.status_code == 200


def test_username_rules(client):
    for bad in ["ab", "Has Space", "dash-name", "x" * 33]:
        response = client.post(
            "/api/auth/register",
            json={"username": bad, "email": "u@example.com", "password": "correct-horse"},
        )
        assert response.status_code == 422, bad


def test_duplicate_username_conflicts(client):
    register_and_login(client, "taken")
    response = client.post(
        "/api/auth/register",
        json={"username": "TAKEN", "email": "other@example.com", "password": "correct-horse"},
    )
    assert response.status_code == 409


def test_expired_token_is_rejected(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "access_token_minutes", -1)
    headers = register_and_login(client, "late")
    assert client.get("/api/auth/me", headers=headers).status_code == 401
