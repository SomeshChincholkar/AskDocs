import asyncio
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.auth import AuthenticatedUser, create_access_token, hash_password
from tests.auth_support import FakePool, new_user

TEST_SECRET = "api-test-secret-with-at-least-thirty-two-characters"


def settings_for_tests():
    return Settings(
        database_url="",
        gemini_api_key="",
        jwt_secret=TEST_SECRET,
        login_attempts_per_15_min=1000,
        anon_create_per_hour=1000,
        _env_file=None,
    )


def token_for(user):
    return create_access_token(user, settings_for_tests())[0]


def build_client(pool):
    application = create_app(settings_for_tests())
    application.state.pool = pool
    return TestClient(application)


def test_anonymous_token_auth_and_no_cookies():
    pool = FakePool()
    client = build_client(pool)

    page = client.get("/")
    anonymous_response = client.post("/api/auth/anonymous")
    missing_token = client.get("/api/auth/me")
    assert page.status_code == 200
    assert anonymous_response.status_code == 200
    assert "merged" not in anonymous_response.json()
    assert missing_token.status_code == 401
    assert missing_token.json() == {"detail": "Not authenticated", "code": "no_token"}

    token = anonymous_response.json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["is_anonymous"] is True
    for response in (page, anonymous_response, missing_token, me):
        assert "set-cookie" not in response.headers
        assert response.headers["content-security-policy"] == (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; frame-ancestors 'none'"
        )
    script = client.get("/static/js/app.js")
    assert script.status_code == 200
    assert "askdocs_token" in script.text
    assert "sessionStorage" not in script.text
    assert "innerHTML" not in script.text
    assert script.text.count("fetch(") == 1
    assert "credentials: \"omit\"" in script.text


def test_anonymous_creation_is_rate_limited_per_client_ip():
    settings = Settings(
        jwt_secret=TEST_SECRET,
        anon_create_per_hour=1,
        _env_file=None,
    )
    application = create_app(settings)
    application.state.pool = FakePool()
    client = TestClient(application)
    headers = {"X-Forwarded-For": "192.0.2.77"}

    first = client.post("/api/auth/anonymous", headers=headers)
    second = client.post("/api/auth/anonymous", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 429


def test_register_upgrades_anonymous_user_in_place_and_revokes_old_token():
    pool = FakePool()
    client = build_client(pool)
    anonymous_response = client.post("/api/auth/anonymous")
    anonymous_token = anonymous_response.json()["access_token"]
    anonymous_id = UUID(anonymous_response.json()["user"]["id"])
    pool.sessions.append({"id": uuid4(), "user_id": anonymous_id, "title": "kept"})

    registered = client.post(
        "/api/auth/register",
        json={"username": "reader.name", "password": "a secure password"},
        headers={"Authorization": f"Bearer {anonymous_token}"},
    )
    assert registered.status_code == 201
    body = registered.json()
    assert UUID(body["user"]["id"]) == anonymous_id
    assert body["user"]["is_anonymous"] is False
    assert body["merged"]["sessions"] == 1
    assert pool.sessions[0]["user_id"] == anonymous_id
    assert pool.users[anonymous_id]["token_version"] == 1

    old_token = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {anonymous_token}"},
    )
    assert old_token.status_code == 401
    assert old_token.json()["code"] == "token_invalid"
    current = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert current.json()["username"] == "reader.name"


def test_register_rejects_case_insensitive_duplicate_username():
    pool = FakePool()
    new_user(pool, uuid4(), "TakenName", "hash", anonymous=False)
    anonymous_id = uuid4()
    new_user(pool, anonymous_id)
    client = build_client(pool)
    token = token_for(AuthenticatedUser(anonymous_id, None, True, 0))

    response = client.post(
        "/api/auth/register",
        json={"username": "takenname", "password": "a secure password"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "That username is already taken."
    assert pool.users[anonymous_id]["is_anonymous"] is True


def test_login_merges_anonymous_data_to_registered_account_and_scopes_sessions():
    pool = FakePool()
    account_id, anonymous_id = uuid4(), uuid4()
    password_hash = asyncio.run(hash_password("account password"))
    new_user(pool, account_id, "account", password_hash, anonymous=False)
    new_user(pool, anonymous_id)
    for index in range(2):
        pool.sessions.append(
            {"id": uuid4(), "user_id": anonymous_id, "title": f"chat {index}", "updated_at": index}
        )
    for index in range(2):
        pool.documents.append({"id": uuid4(), "user_id": anonymous_id, "filename": str(index)})
        pool.chunks.append({"id": uuid4(), "user_id": anonymous_id})
    old_session_times = [item["updated_at"] for item in pool.sessions]
    client = build_client(pool)
    anonymous_token = token_for(AuthenticatedUser(anonymous_id, None, True, 0))

    response = client.post(
        "/api/auth/login",
        json={"username": "ACCOUNT", "password": "account password"},
        headers={"Authorization": f"Bearer {anonymous_token}"},
    )
    assert response.status_code == 200
    assert response.json()["merged"] == {"sessions": 2, "documents": 2}
    assert anonymous_id not in pool.users
    assert all(
        row["user_id"] == account_id
        for row in pool.sessions + pool.documents + pool.chunks
    )
    assert [item["updated_at"] for item in pool.sessions] == old_session_times

    owner_sessions = client.get(
        "/api/sessions",
        headers={"Authorization": f"Bearer {response.json()['access_token']}"},
    )
    outsider_id = uuid4()
    new_user(pool, outsider_id, "outsider", password_hash, anonymous=False)
    outsider_token = token_for(AuthenticatedUser(outsider_id, "outsider", False, 0))
    outsider_sessions = client.get(
        "/api/sessions",
        headers={"Authorization": f"Bearer {outsider_token}"},
    )
    assert len(owner_sessions.json()) == 2
    assert outsider_sessions.json() == []
    private_session = client.get(
        f"/api/sessions/{pool.sessions[0]['id']}/messages",
        headers={"Authorization": f"Bearer {outsider_token}"},
    )
    assert private_session.status_code == 404


def test_invalid_or_registered_token_is_not_used_as_merge_source():
    pool = FakePool()
    account_id, registered_source_id = uuid4(), uuid4()
    password_hash = asyncio.run(hash_password("account password"))
    new_user(pool, account_id, "target", password_hash, anonymous=False)
    new_user(pool, registered_source_id, "other", password_hash, anonymous=False)
    pool.sessions.append({"id": uuid4(), "user_id": registered_source_id, "title": "private"})
    client = build_client(pool)

    invalid_token_login = client.post(
        "/api/auth/login",
        json={"username": "target", "password": "account password"},
        headers={"Authorization": "Bearer invalid-token"},
    )
    assert invalid_token_login.status_code == 200
    assert invalid_token_login.json()["merged"] == {"sessions": 0, "documents": 0}

    registered_token = token_for(
        AuthenticatedUser(registered_source_id, "other", False, 0)
    )
    registered_token_login = client.post(
        "/api/auth/login",
        json={"username": "target", "password": "account password"},
        headers={"Authorization": f"Bearer {registered_token}"},
    )
    assert registered_token_login.status_code == 200
    assert registered_token_login.json()["merged"] == {"sessions": 0, "documents": 0}
    assert pool.sessions[0]["user_id"] == registered_source_id


def test_merge_failure_rolls_back_but_login_succeeds():
    pool = FakePool()
    account_id, anonymous_id = uuid4(), uuid4()
    password_hash = asyncio.run(hash_password("account password"))
    new_user(pool, account_id, "target-rollback", password_hash, anonymous=False)
    new_user(pool, anonymous_id)
    pool.sessions.append({"id": uuid4(), "user_id": anonymous_id, "title": "still here"})
    pool.documents.append({"id": uuid4(), "user_id": anonymous_id, "filename": "file.txt"})
    pool.fail_document_merge = True
    client = build_client(pool)
    anonymous_token = token_for(AuthenticatedUser(anonymous_id, None, True, 0))

    response = client.post(
        "/api/auth/login",
        json={"username": "target-rollback", "password": "account password"},
        headers={"Authorization": f"Bearer {anonymous_token}"},
    )
    assert response.status_code == 200
    assert response.json()["merged"] == {"sessions": 0, "documents": 0}
    assert anonymous_id in pool.users
    assert pool.sessions[0]["user_id"] == anonymous_id
    assert pool.documents[0]["user_id"] == anonymous_id


def test_login_failure_is_generic_and_invalid_username_is_rejected():
    pool = FakePool()
    password_hash = asyncio.run(hash_password("account password"))
    account_id = uuid4()
    new_user(pool, account_id, "generic", password_hash, anonymous=False)
    client = build_client(pool)
    missing = client.post(
        "/api/auth/login",
        json={"username": "notfound", "password": "wrong password"},
    )
    wrong = client.post(
        "/api/auth/login",
        json={"username": "generic", "password": "wrong password"},
    )
    invalid_username = client.post(
        "/api/auth/register",
        json={"username": "bad name", "password": "long enough"},
    )
    assert missing.status_code == wrong.status_code == 401
    assert missing.json()["detail"] == wrong.json()["detail"] == "Invalid username or password"
    assert invalid_username.status_code == 422


def test_logout_all_invalidates_registered_tokens_and_rejects_anonymous_user():
    pool = FakePool()
    account_id = uuid4()
    new_user(pool, account_id, "logout-user", "hash", anonymous=False)
    account_token = token_for(AuthenticatedUser(account_id, "logout-user", False, 0))
    anonymous_id = uuid4()
    new_user(pool, anonymous_id)
    anonymous_token = token_for(AuthenticatedUser(anonymous_id, None, True, 0))
    client = build_client(pool)

    logout = client.post(
        "/api/auth/logout-all",
        headers={"Authorization": f"Bearer {account_token}"},
    )
    assert logout.status_code == 200 and logout.json() == {"ok": True}
    invalidated = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {account_token}"},
    )
    anonymous_logout = client.post(
        "/api/auth/logout-all",
        headers={"Authorization": f"Bearer {anonymous_token}"},
    )
    assert invalidated.status_code == 401
    assert invalidated.json()["code"] == "token_invalid"
    assert anonymous_logout.status_code == 403


def test_login_rate_limits_by_ip_and_username():
    settings = Settings(
        jwt_secret=TEST_SECRET,
        login_attempts_per_15_min=1,
        anon_create_per_hour=100,
        _env_file=None,
    )
    application = create_app(settings)
    application.state.pool = FakePool()
    client = TestClient(application)

    first_ip_attempt = client.post(
        "/api/auth/login",
        json={"username": "missing-one", "password": "wrong password"},
        headers={"X-Forwarded-For": "192.0.2.10"},
    )
    same_ip_attempt = client.post(
        "/api/auth/login",
        json={"username": "missing-two", "password": "wrong password"},
        headers={"X-Forwarded-For": "192.0.2.10"},
    )
    other_ip_same_name = client.post(
        "/api/auth/login",
        json={"username": "other-missing", "password": "wrong password"},
        headers={"X-Forwarded-For": "192.0.2.11"},
    )
    same_username_other_ip = client.post(
        "/api/auth/login",
        json={"username": "other-missing", "password": "wrong password"},
        headers={"X-Forwarded-For": "192.0.2.12"},
    )
    assert first_ip_attempt.status_code == 401
    assert same_ip_attempt.status_code == 429
    assert other_ip_same_name.status_code == 401
    assert same_username_other_ip.status_code == 429
