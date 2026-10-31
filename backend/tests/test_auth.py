from datetime import datetime, timedelta, timezone

from jose import jwt
from sqlalchemy import text

from app.core.config import settings
from app.database.session import engine
from tests.conftest import PASSWORD, auth_headers, register


def test_register_login_me_roundtrip(client):
    res = register(client, "Jo@Example.com")
    assert res.status_code == 200
    body = res.json()
    assert body["user"]["email"] == "jo@example.com"  # normalised
    assert body["user"]["role"] == "teacher"
    assert "password" not in res.text and "password_hash" not in res.text

    login = client.post("/auth/login", json={"email": "JO@example.com", "password": PASSWORD})
    assert login.status_code == 200
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {login.json()['access_token']}"})
    assert me.status_code == 200
    assert set(me.json()) == {"id", "email", "full_name", "role"}


def test_password_is_hashed_in_db(client):
    register(client, "hash@example.com")
    with engine.connect() as c:
        stored = c.execute(text("SELECT password_hash FROM users WHERE email='hash@example.com'")).scalar()
    assert stored != PASSWORD and stored.startswith("$2")


def test_duplicate_email_rejected_case_insensitively(client):
    assert register(client, "dup@example.com").status_code == 200
    again = register(client, "DUP@example.com")
    assert again.status_code == 409 and "already registered" in again.json()["detail"]


def test_registration_validation(client):
    base = {"email": "v@example.com", "full_name": "Vee", "password": PASSWORD, "confirm_password": PASSWORD}
    cases = {
        "mismatch": {**base, "confirm_password": "something-else-entirely"},
        "bad email": {**base, "email": "not-an-email"},
        "short password": {**base, "password": "short", "confirm_password": "short"},
        "over 72 bytes": {**base, "password": "x" * 73, "confirm_password": "x" * 73},
        "bad role": {**base, "role": "superuser"},
        "short name": {**base, "full_name": " a "},
    }
    for label, body in cases.items():
        assert client.post("/auth/register", json=body).status_code == 422, label


def test_admin_registration_needs_invite_code(client, monkeypatch):
    assert register(client, "a1@example.com", role="admin").status_code == 403
    assert register(client, "a2@example.com", role="admin", admin_invite_code="wrong").status_code == 403
    ok = register(client, "a3@example.com", role="admin", admin_invite_code="let-me-in-123")
    assert ok.status_code == 200 and ok.json()["user"]["role"] == "admin"
    monkeypatch.setattr(settings, "admin_invite_code", None)  # feature off => nobody can self-register as admin
    assert register(client, "a4@example.com", role="admin", admin_invite_code="let-me-in-123").status_code == 403


def test_login_failures_are_indistinguishable(client):
    register(client, "real@example.com")
    wrong_pw = client.post("/auth/login", json={"email": "real@example.com", "password": "not-the-password"})
    unknown = client.post("/auth/login", json={"email": "ghost@example.com", "password": PASSWORD})
    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.json() == unknown.json()


def test_protected_routes_reject_missing_bad_and_expired_tokens(client):
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401
    good = auth_headers(client)
    forged = jwt.encode({"sub": "1", "role": "admin", "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, "wrong-secret", algorithm="HS256")
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    expired = jwt.encode({"sub": "1", "role": "teacher", "exp": datetime.now(timezone.utc) - timedelta(minutes=1)}, settings.jwt_secret, algorithm="HS256")
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    no_sub = jwt.encode({"role": "teacher", "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, settings.jwt_secret, algorithm="HS256")
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {no_sub}"}).status_code == 401
    assert client.get("/auth/me", headers=good).status_code == 200


def test_deactivated_user_loses_access(client):
    headers = auth_headers(client)
    with engine.begin() as c:
        c.execute(text("UPDATE users SET is_active = 0"))
    assert client.get("/auth/me", headers=headers).status_code == 401
    assert client.post("/auth/login", json={"email": "teacher@example.com", "password": PASSWORD}).status_code == 403


def test_role_based_access_on_admin_routes(client):
    teacher = auth_headers(client, "t@example.com")
    admin = auth_headers(client, "ad@example.com", role="admin", admin_invite_code="let-me-in-123")
    url = "/admin/media/lesson/999"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=teacher).status_code == 403
    assert client.get(url, headers=admin).status_code == 404  # authorised; lesson simply doesn't exist


def test_forgot_password_sends_recovery_code_from_meridian_email_and_resets_password(client, monkeypatch):
    from app.routers import auth as auth_router
    from app.services.email_service import build_password_reset_message

    auth_router._email_request_log.clear()
    register(client, "recover@example.com", name="Asha Rao")
    captured: list[dict] = []

    def fake_send(*, to_email: str, full_name: str, code: str) -> None:
        msg = build_password_reset_message(
            to_email=to_email,
            full_name=full_name,
            code=code,
            expires_minutes=settings.reset_code_expire_minutes,
        )
        captured.append(
            {
                "to_email": to_email,
                "full_name": full_name,
                "code": code,
                "from": msg["From"],
                "subject": msg["Subject"],
                "body": msg.get_body(preferencelist=("plain",)).get_content(),
            }
        )

    monkeypatch.setattr(auth_router, "send_password_reset_email", fake_send)

    forgot = client.post("/auth/forgot-password", json={"email": "RECOVER@example.com"})
    assert forgot.status_code == 200, forgot.text
    data = forgot.json()
    assert data["sender_email"] == "meridianschool.wgl@gmail.com"
    assert data["message"] == "Recovery code sent successfully. Please check your inbox."
    assert len(captured) == 1
    assert captured[0]["to_email"] == "recover@example.com"
    assert "meridianschool.wgl@gmail.com" in captured[0]["from"]
    assert "Verba British Scoring" in captured[0]["from"]
    assert captured[0]["subject"] == "Your Verba Password Recovery Code"
    assert "Verba British Scoring Team" in captured[0]["body"]
    code = captured[0]["code"]
    assert len(code) == 6 and code.isdigit()
    # Never expose recovery code in API response.
    assert code not in forgot.text
    assert "dev_recovery_code" not in data

    # Plaintext code is never stored in DB.
    with engine.connect() as c:
        stored_hash = c.execute(text("SELECT code_hash FROM password_reset_codes")).scalar()
    assert stored_hash and stored_hash != code

    # Verify reset code endpoint works without consuming the code.
    verify_ok = client.post("/auth/verify-reset-code", json={"email": "recover@example.com", "code": code})
    assert verify_ok.status_code == 200 and verify_ok.json()["valid"] is True

    # Reset password with the 6-digit recovery code.
    new_pw = "brand-new-meridian-password-2026"
    reset = client.post(
        "/auth/reset-password",
        json={
            "email": "recover@example.com",
            "code": code,
            "password": new_pw,
            "confirm_password": new_pw,
        },
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["user"]["email"] == "recover@example.com"
    assert reset.json()["access_token"]

    # Code cannot be reused after resetting password.
    reuse = client.post(
        "/auth/reset-password",
        json={
            "email": "recover@example.com",
            "code": code,
            "password": "another-valid-password-123",
            "confirm_password": "another-valid-password-123",
        },
    )
    assert reuse.status_code == 400

    # Old password no longer works; new password succeeds.
    assert client.post("/auth/login", json={"email": "recover@example.com", "password": PASSWORD}).status_code == 401
    assert client.post("/auth/login", json={"email": "recover@example.com", "password": new_pw}).status_code == 200


def test_forgot_password_resend_expiry_lockout_and_smtp_failure(client, monkeypatch):
    from app.routers import auth as auth_router
    from app.services.email_service import EmailDeliveryError, USER_SAFE_DELIVERY_ERROR

    auth_router._email_request_log.clear()
    captured: list[str] = []
    monkeypatch.setattr(
        auth_router,
        "send_password_reset_email",
        lambda *, to_email, full_name, code: captured.append(code),
    )

    # Unregistered email returns identical response without sending email or leaking registration status.
    unregistered = client.post("/auth/forgot-password", json={"email": "ghost@example.com"})
    assert unregistered.status_code == 200
    assert unregistered.json()["message"] == "Recovery code sent successfully. Please check your inbox."
    assert len(captured) == 0

    register(client, "lockout@example.com")
    assert client.post("/auth/forgot-password", json={"email": "lockout@example.com"}).status_code == 200
    first_code = captured[-1]

    # Resend invalidates previous recovery code.
    assert client.post("/auth/forgot-password", json={"email": "lockout@example.com"}).status_code == 200
    second_code = captured[-1]
    assert client.post("/auth/verify-reset-code", json={"email": "lockout@example.com", "code": first_code}).status_code == 400
    assert client.post("/auth/verify-reset-code", json={"email": "lockout@example.com", "code": second_code}).status_code == 200

    # Exhaust max attempts (default 5) with a wrong code.
    for _ in range(settings.reset_code_max_attempts):
        bad = client.post("/auth/verify-reset-code", json={"email": "lockout@example.com", "code": "000000"})
        assert bad.status_code == 400

    # Even the real code is now locked out until a new one is requested.
    locked = client.post("/auth/verify-reset-code", json={"email": "lockout@example.com", "code": second_code})
    assert locked.status_code == 400

    # Expired code is rejected.
    assert client.post("/auth/forgot-password", json={"email": "lockout@example.com"}).status_code == 200
    third_code = captured[-1]
    with engine.begin() as c:
        c.execute(text("UPDATE password_reset_codes SET expires_at = '2000-01-01 00:00:00'"))
    assert client.post("/auth/verify-reset-code", json={"email": "lockout@example.com", "code": third_code}).status_code == 400

    # Rate limit blocks excessive requests within the 15-minute window.
    monkeypatch.setattr(settings, "reset_code_max_requests_per_window", 3)
    rate_limited = client.post("/auth/forgot-password", json={"email": "lockout@example.com"})
    assert rate_limited.status_code == 429

    # SMTP failure returns 503 with user-safe error message.
    register(client, "smtpfail@example.com")

    def fail_send(*, to_email: str, full_name: str, code: str) -> None:
        raise EmailDeliveryError(USER_SAFE_DELIVERY_ERROR)

    monkeypatch.setattr(auth_router, "send_password_reset_email", fail_send)
    failed = client.post("/auth/forgot-password", json={"email": "smtpfail@example.com"})
    assert failed.status_code == 503
    assert failed.json()["detail"] == "Unable to send the recovery email right now. Please try again later."

