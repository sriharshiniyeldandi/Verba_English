"""Email delivery service for Verba British Scoring password recovery codes.

All password-recovery emails are sent from ``meridianschool.wgl@gmail.com``
via Gmail SMTP with STARTTLS (with automatic fallback to SSL port 465 if
port 587 is intercepted by a network firewall), reading credentials from
environment variables or ``backend/.env``. Recovery codes and SMTP passwords
are never logged or exposed in responses.
"""
from email.message import EmailMessage
from email.utils import formataddr
import html
import logging
import smtplib
import ssl

from app.core.config import settings

logger = logging.getLogger("verba.email")

DEFAULT_RECOVERY_SENDER = "meridianschool.wgl@gmail.com"
DEFAULT_SENDER_NAME = "Verba British Scoring"
USER_SAFE_DELIVERY_ERROR = "Unable to send the recovery email right now. Please try again later."


class EmailDeliveryError(Exception):
    """Raised with a user-safe message when a recovery email cannot be dispatched."""


def get_recovery_sender_email() -> str:
    return (settings.smtp_from_email or DEFAULT_RECOVERY_SENDER).strip() or DEFAULT_RECOVERY_SENDER


def get_smtp_username() -> str:
    return (
        (settings.smtp_username or "").strip()
        or (settings.smtp_user or "").strip()
        or get_recovery_sender_email()
    )


def build_password_reset_message(*, to_email: str, full_name: str, code: str, expires_minutes: int) -> EmailMessage:
    sender_email = get_recovery_sender_email()
    sender_name = (settings.smtp_from_name or DEFAULT_SENDER_NAME).strip() or DEFAULT_SENDER_NAME
    safe_code = html.escape(code)

    msg = EmailMessage()
    msg["Subject"] = "Your Verba Password Recovery Code"
    msg["From"] = formataddr((sender_name, sender_email))
    msg["To"] = to_email
    msg["Reply-To"] = sender_email

    plain_body = (
        "Hello,\n\n"
        "We received a request to reset your Verba British Scoring account password.\n\n"
        "Your 6-digit recovery code is:\n\n"
        f"{code}\n\n"
        f"This code is valid for {expires_minutes} minutes.\n\n"
        "If you did not request this password reset, please ignore this email.\n\n"
        "Regards,\n"
        "Verba British Scoring Team"
    )
    msg.set_content(plain_body)

    html_body = f"""\
<!DOCTYPE html>
<html lang="en">
  <body style="margin:0;padding:24px;background-color:#f7f3ea;font-family:'DM Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#253025;">
    <div style="max-width:480px;margin:0 auto;background-color:#fcfaf5;border:1px solid #ded9ce;border-radius:12px;padding:32px;">
      <p style="font-size:11px;font-weight:700;letter-spacing:1.35px;color:#687a52;margin:0 0 12px;text-transform:uppercase;">
        Verba British Scoring
      </p>
      <h1 style="font-family:Georgia,serif;font-size:24px;margin:0 0 16px;color:#253025;">
        Your Verba Password Recovery Code
      </h1>
      <p style="font-size:14px;line-height:1.6;color:#253025;margin:0 0 12px;">
        Hello,
      </p>
      <p style="font-size:14px;line-height:1.6;color:#697269;margin:0 0 18px;">
        We received a request to reset your Verba British Scoring account password.
      </p>
      <p style="font-size:14px;line-height:1.6;color:#253025;margin:0 0 10px;">
        Your 6-digit recovery code is:
      </p>
      <div style="background-color:#f0f3ea;border:1px solid #c4ccb9;border-radius:10px;padding:20px;text-align:center;margin:0 0 20px;">
        <strong style="font-size:32px;letter-spacing:8px;color:#3f4f3a;font-family:monospace;">
          {safe_code}
        </strong>
      </div>
      <p style="font-size:14px;line-height:1.6;color:#253025;margin:0 0 14px;">
        This code is valid for <strong>{expires_minutes} minutes</strong>.
      </p>
      <p style="font-size:13px;line-height:1.6;color:#697269;margin:0 0 20px;">
        If you did not request this password reset, please ignore this email.
      </p>
      <p style="font-size:14px;line-height:1.6;color:#253025;border-top:1px solid #ded9ce;padding-top:16px;margin:0;">
        Regards,<br />
        <strong>Verba British Scoring Team</strong>
      </p>
    </div>
  </body>
</html>
"""
    msg.add_alternative(html_body, subtype="html")
    return msg


def _normalized_smtp_password() -> str:
    raw_password = (settings.smtp_password or "").strip()
    if "gmail.com" in (settings.smtp_host or "").lower():
        return raw_password.replace(" ", "")
    return raw_password


def ensure_smtp_configured() -> None:
    """Verify required SMTP settings are present without logging any secrets."""
    if not _normalized_smtp_password() or not (settings.smtp_host or "").strip() or not get_smtp_username():
        logger.error("SMTP email delivery is not configured (missing SMTP_PASSWORD or SMTP host/username).")
        raise EmailDeliveryError(USER_SAFE_DELIVERY_ERROR)


def _send_via_port(
    *,
    host: str,
    port: int,
    smtp_user: str,
    smtp_password: str,
    msg: EmailMessage,
    context: ssl.SSLContext,
) -> dict:
    if port == 465:
        with smtplib.SMTP_SSL(host, port, local_hostname="localhost", timeout=15, context=context) as server:
            server.ehlo()
            server.login(smtp_user, smtp_password)
            return server.send_message(msg)
    with smtplib.SMTP(host, port, local_hostname="localhost", timeout=15) as server:
        server.ehlo()
        if settings.smtp_use_tls:
            server.starttls(context=context)
            server.ehlo()
        server.login(smtp_user, smtp_password)
        return server.send_message(msg)


def send_password_reset_email(*, to_email: str, full_name: str, code: str) -> None:
    """Send the 6-digit recovery code to ``to_email`` via Gmail SMTP STARTTLS (with SSL 465 fallback)."""
    ensure_smtp_configured()

    smtp_user = get_smtp_username()
    smtp_password = _normalized_smtp_password()
    host = (settings.smtp_host or "smtp.gmail.com").strip()
    port = int(settings.smtp_port or 587)

    msg = build_password_reset_message(
        to_email=to_email,
        full_name=full_name,
        code=code,
        expires_minutes=settings.reset_code_expire_minutes,
    )

    context = ssl.create_default_context()
    ports_to_try = [port, 465] if (port == 587 and "gmail.com" in host.lower()) else [port]
    last_exc: Exception | None = None

    for candidate_port in ports_to_try:
        try:
            refused = _send_via_port(
                host=host,
                port=candidate_port,
                smtp_user=smtp_user,
                smtp_password=smtp_password,
                msg=msg,
                context=context,
            )
            if refused:
                logger.error("SMTP server refused recipient for password reset email.")
                raise EmailDeliveryError(USER_SAFE_DELIVERY_ERROR)
            return
        except EmailDeliveryError:
            raise
        except smtplib.SMTPAuthenticationError as exc:
            logger.error(
                "SMTP authentication failed for %s on %s:%s. "
                "Note: Gmail requires a 16-character Google App Password (https://myaccount.google.com/apppasswords), "
                "not the regular account login password.",
                smtp_user,
                host,
                candidate_port,
            )
            raise EmailDeliveryError(USER_SAFE_DELIVERY_ERROR) from exc
        except (smtplib.SMTPException, OSError) as exc:
            last_exc = exc
            logger.warning(
                "SMTP attempt via %s:%s failed (%s: %s).",
                host,
                candidate_port,
                type(exc).__name__,
                exc,
            )

    logger.error(
        "All SMTP delivery attempts to %s failed (%s: %s). "
        "Check whether outbound ports 587/465 are blocked by a firewall or antivirus.",
        host,
        type(last_exc).__name__ if last_exc else "UnknownError",
        last_exc,
    )
    raise EmailDeliveryError(USER_SAFE_DELIVERY_ERROR) from last_exc
