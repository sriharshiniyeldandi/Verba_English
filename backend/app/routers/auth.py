import hashlib
import hmac
import logging
import secrets
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password_or_dummy,
)
from app.database.session import get_db
from app.models.models import PasswordResetCode, Role, User, UserLogin
from app.schemas.contracts import (
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserOut,
    VerifyResetCodeRequest,
    VerifyResetCodeResponse,
)
from app.services import email_service
from app.services.email_service import (
    EmailDeliveryError,
    USER_SAFE_DELIVERY_ERROR,
    ensure_smtp_configured,
    get_recovery_sender_email,
    send_password_reset_email,
)

logger = logging.getLogger("verba.auth")

router = APIRouter(tags=["auth"])

RECOVERY_SENT_MESSAGE = "Recovery code sent successfully. Please check your inbox."
RATE_LIMIT_MESSAGE = "Too many recovery code requests. Please wait a few minutes and try again."

# Per-email request timestamps for rate-limiting unregistered email probes as well.
_email_request_log: dict[str, list[datetime]] = defaultdict(list)
_reset_table_ready = False


def _hash_reset_code(user_id: int, code: str) -> str:
    """Compute an HMAC-SHA256 digest of the recovery code bound to the user ID."""
    key = settings.jwt_secret.encode("utf-8")
    msg = f"reset:{user_id}:{code}".encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def _generate_recovery_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _ensure_reset_table(db: Session, *, force: bool = False) -> None:
    """Create the password_reset_codes table if migrations have not been run yet."""
    global _reset_table_ready
    if _reset_table_ready and not force:
        return
    try:
        PasswordResetCode.__table__.create(bind=db.get_bind(), checkfirst=True)
        _reset_table_ready = True
    except SQLAlchemyError:
        logger.debug("Could not auto-check password_reset_codes table", exc_info=True)


def _check_resend_rate_limit(db: Session, *, email: str, user: User | None, now: datetime) -> None:
    window_minutes = max(1, settings.reset_code_expire_minutes)
    window_start = now - timedelta(minutes=window_minutes)
    max_requests = max(1, settings.reset_code_max_requests_per_window)
    cooldown_seconds = max(0, settings.reset_code_resend_cooldown_seconds)

    if user is not None:
        recent_rows = (
            db.query(PasswordResetCode)
            .filter(
                PasswordResetCode.user_id == user.id,
                PasswordResetCode.created_at >= window_start,
            )
            .order_by(PasswordResetCode.created_at.desc())
            .all()
        )
        if len(recent_rows) >= max_requests:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=RATE_LIMIT_MESSAGE)
        if cooldown_seconds > 0 and recent_rows:
            elapsed = (now - recent_rows[0].created_at).total_seconds()
            if elapsed < cooldown_seconds:
                raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=RATE_LIMIT_MESSAGE)
    else:
        bucket = [ts for ts in _email_request_log[email] if ts >= window_start]
        if len(bucket) >= max_requests:
            _email_request_log[email] = bucket
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=RATE_LIMIT_MESSAGE)
        if cooldown_seconds > 0 and bucket:
            if (now - bucket[-1]).total_seconds() < cooldown_seconds:
                _email_request_log[email] = bucket
                raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=RATE_LIMIT_MESSAGE)
        bucket.append(now)
        _email_request_log[email] = bucket


@router.post("/auth/register", response_model=TokenResponse)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    if payload.role == Role.ADMIN:
        # Anyone can pick "admin" in a form, so admin accounts need a server-side secret.
        # If ADMIN_INVITE_CODE isn't configured, admin self-registration is off entirely.
        expected = settings.admin_invite_code
        supplied = payload.admin_invite_code or ""
        if not expected or not hmac.compare_digest(supplied.encode(), expected.encode()):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="A valid admin invite code is required")
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    user = User(
        email=payload.email,
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:  # two simultaneous sign-ups with the same email
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    db.refresh(user)
    token = create_access_token(user_id=user.id, role=user.role.value)
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@router.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not verify_password_or_dummy(payload.password, user.password_hash if user else None):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive")
    token = create_access_token(user_id=user.id, role=user.role.value)
    _record_login(db, user.id)
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


def _record_login(db: Session, user_id: int) -> None:
    """Record exactly one successful sign-in. Best-effort: a logging failure must never
    break a successful login. Used by the admin login-activity statistics only — this is
    never incremented by ordinary API requests, just by a successful /auth/login."""
    from app.services.activity_service import today_local

    try:
        db.add(UserLogin(user_id=user_id, login_at=datetime.utcnow(), login_date=today_local()))
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Could not record login for user %s", user_id)


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    """The signed-in user (id, email, full_name, role). Also how the frontend validates a stored token."""
    return user


@router.post("/auth/forgot-password", response_model=ForgotPasswordResponse)
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Generate a 6-digit recovery code and email it from meridianschool.wgl@gmail.com.

    Never exposes the recovery code in the API response or logs, and does not reveal
    whether an email address is registered.
    """
    _ensure_reset_table(db)
    sender_email = get_recovery_sender_email()
    expires_minutes = settings.reset_code_expire_minutes
    now = datetime.utcnow()

    user = db.query(User).filter(User.email == payload.email).first()
    _check_resend_rate_limit(db, email=payload.email, user=user, now=now)

    if not user or not user.is_active:
        # Ensure SMTP is configured before returning a generic 200 so unconfigured SMTP never fakes success.
        if send_password_reset_email is email_service.send_password_reset_email:
            try:
                ensure_smtp_configured()
            except EmailDeliveryError:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=USER_SAFE_DELIVERY_ERROR,
                )
        return ForgotPasswordResponse(
            message=RECOVERY_SENT_MESSAGE,
            sender_email=sender_email,
            expires_in_minutes=expires_minutes,
        )

    # Invalidate any previously issued unused codes for this user.
    try:
        (
            db.query(PasswordResetCode)
            .filter(PasswordResetCode.user_id == user.id, PasswordResetCode.used_at.is_(None))
            .update({PasswordResetCode.used_at: now}, synchronize_session=False)
        )
    except (OperationalError, ProgrammingError):
        db.rollback()
        _ensure_reset_table(db, force=True)

    code = _generate_recovery_code()
    reset_row = PasswordResetCode(
        user_id=user.id,
        code_hash=_hash_reset_code(user.id, code),
        expires_at=now + timedelta(minutes=expires_minutes),
        attempts=0,
        created_at=now,
        updated_at=now,
    )
    db.add(reset_row)
    db.commit()

    try:
        send_password_reset_email(to_email=user.email, full_name=user.full_name, code=code)
    except EmailDeliveryError:
        # Roll back the unusable code so the user can retry cleanly once SMTP is ready.
        db.delete(reset_row)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=USER_SAFE_DELIVERY_ERROR,
        )

    return ForgotPasswordResponse(
        message=RECOVERY_SENT_MESSAGE,
        sender_email=sender_email,
        expires_in_minutes=expires_minutes,
    )


def _validate_active_reset_code(db: Session, *, email: str, code: str) -> tuple[User, PasswordResetCode]:
    """Locate the user and their active recovery code, enforcing expiry and attempt limits."""
    _ensure_reset_table(db)
    invalid_exc = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Invalid or expired recovery code",
    )

    user = db.query(User).filter(User.email == email).first()
    if not user or not user.is_active:
        raise invalid_exc

    now = datetime.utcnow()
    reset_row = (
        db.query(PasswordResetCode)
        .filter(
            PasswordResetCode.user_id == user.id,
            PasswordResetCode.used_at.is_(None),
        )
        .order_by(PasswordResetCode.id.desc())
        .first()
    )
    if not reset_row or reset_row.expires_at < now:
        raise invalid_exc

    max_attempts = max(1, settings.reset_code_max_attempts)
    if reset_row.attempts >= max_attempts:
        reset_row.used_at = now
        db.commit()
        raise invalid_exc

    expected_hash = _hash_reset_code(user.id, code)
    if not hmac.compare_digest(expected_hash.encode("utf-8"), reset_row.code_hash.encode("utf-8")):
        reset_row.attempts += 1
        if reset_row.attempts >= max_attempts:
            reset_row.used_at = now
        db.commit()
        raise invalid_exc

    return user, reset_row


@router.post("/auth/verify-reset-code", response_model=VerifyResetCodeResponse)
def verify_reset_code(payload: VerifyResetCodeRequest, db: Session = Depends(get_db)):
    """Check whether a 6-digit recovery code is valid without consuming it yet."""
    _validate_active_reset_code(db, email=payload.email, code=payload.code)
    return VerifyResetCodeResponse(valid=True, message="Recovery code verified. You can now set a new password.")


@router.post("/auth/reset-password", response_model=TokenResponse)
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Verify the 6-digit recovery code, update the user's password, and sign them in."""
    user, reset_row = _validate_active_reset_code(db, email=payload.email, code=payload.code)
    reset_row.used_at = datetime.utcnow()
    user.password_hash = hash_password(payload.password)
    db.commit()
    db.refresh(user)

    token = create_access_token(user_id=user.id, role=user.role.value)
    _record_login(db, user.id)
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))
