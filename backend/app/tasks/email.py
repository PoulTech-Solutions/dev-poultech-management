"""Email tasks. The only worker code that touches SMTP.

Two rules, both deliberate:
  * the OTP body arrives sealed - Redis also holds the copy, so the task
    argument must not be plaintext; it is decrypted here, used, then dropped
  * the body is never logged, and `to` is masked by the logging redactor
"""

from __future__ import annotations

import html
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

import structlog
from celery import Task

from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.core.security import decrypt_otp_payload

log = structlog.get_logger()

_RETRYABLE = (
    smtplib.SMTPServerDisconnected,
    smtplib.SMTPConnectError,
    smtplib.SMTPDataError,
    TimeoutError,
    OSError,
)

_PURPOSE_COPY = {
    'signup': ('Confirm your email', 'Use this code to activate your Poultech account.'),
    'login': ('Sign-in verification', 'Use this code to finish signing in to Poultech.'),
    'reset': ('Reset your password', 'Use this code to set a new password for your account.'),
    'change_password': ('Confirm password change', 'Use this code to confirm changing your password.'),
    'email_change': ('Confirm your new email', 'Use this code to confirm your new email address.'),
    'restore': ('Reactivate your account', 'Use this code to restore your Poultech account.'),
}


def _otp_plain(display_name: str, otp: str, purpose: str, minutes: int) -> str:
    s = get_settings()
    brand = s.smtp_from_name
    title, blurb = _PURPOSE_COPY.get(purpose, ('Verification code', 'Use this code to continue.'))
    return (
        f'Hello {display_name},\n\n'
        f'{title}\n'
        f'{blurb}\n\n'
        f'Your verification code: {otp}\n'
        f'This code works once and expires in {minutes} minutes.\n\n'
        'If you did not request this, ignore this email and secure your account.\n\n'
        f'— {brand}\n'
        f'{s.email_tagline}\n'
    )


def _otp_html(display_name: str, otp: str, purpose: str, minutes: int) -> str:
    s = get_settings()
    brand = html.escape(s.smtp_from_name)
    tagline = html.escape(s.email_tagline)
    title, blurb = _PURPOSE_COPY.get(purpose, ('Verification code', 'Use this code to continue.'))
    name = html.escape(display_name)
    code = html.escape(otp)
    safe_title = html.escape(title)
    safe_blurb = html.escape(blurb)
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{safe_title}</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f3f4f6;padding:32px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e5e7eb;">
          <tr>
            <td style="background:#0f766e;padding:24px 28px;">
              <p style="margin:0;font-size:20px;font-weight:700;color:#ffffff;letter-spacing:0.2px;">{brand}</p>
              <p style="margin:6px 0 0;font-size:13px;color:#ccfbf1;">{tagline}</p>
            </td>
          </tr>
          <tr>
            <td style="padding:28px;">
              <p style="margin:0 0 8px;font-size:16px;color:#111827;">Hello {name},</p>
              <p style="margin:0 0 6px;font-size:18px;font-weight:600;color:#0f766e;">{safe_title}</p>
              <p style="margin:0 0 22px;font-size:14px;line-height:1.55;color:#4b5563;">{safe_blurb}</p>
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:0 0 22px;">
                <tr>
                  <td align="center" style="background:#f0fdfa;border:1px dashed #99f6e4;border-radius:10px;padding:18px;">
                    <p style="margin:0 0 8px;font-size:12px;letter-spacing:0.08em;text-transform:uppercase;color:#0f766e;">Verification code</p>
                    <p style="margin:0;font-size:32px;font-weight:700;letter-spacing:0.28em;color:#134e4a;font-family:Consolas,Monaco,monospace;">{code}</p>
                  </td>
                </tr>
              </table>
              <p style="margin:0 0 8px;font-size:13px;color:#6b7280;">This code works <strong>once</strong> and expires in <strong>{minutes} minutes</strong>.</p>
              <p style="margin:0;font-size:13px;line-height:1.5;color:#9ca3af;">If you did not request this, you can ignore this email. Do not share this code with anyone.</p>
            </td>
          </tr>
          <tr>
            <td style="padding:16px 28px 22px;background:#f9fafb;border-top:1px solid #e5e7eb;">
              <p style="margin:0;font-size:12px;color:#9ca3af;">© {brand} · This is an automated message.</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def _render_otp(to: str, subject: str, *, display_name: str, otp: str, purpose: str, minutes: int) -> EmailMessage:
    s = get_settings()
    msg = EmailMessage()
    msg['From'] = formataddr((s.smtp_from_name, s.smtp_from))
    msg['To'] = to
    msg['Subject'] = subject
    msg['Reply-To'] = s.smtp_from
    msg.set_content(_otp_plain(display_name, otp, purpose, minutes))
    msg.add_alternative(_otp_html(display_name, otp, purpose, minutes), subtype='html')
    return msg


def _render_generic(to: str, subject: str, body: str) -> EmailMessage:
    s = get_settings()
    brand = s.smtp_from_name
    msg = EmailMessage()
    msg['From'] = formataddr((brand, s.smtp_from))
    msg['To'] = to
    msg['Subject'] = subject
    msg['Reply-To'] = s.smtp_from
    msg.set_content(body)
    safe = html.escape(body).replace('\n', '<br>')
    safe_brand = html.escape(brand)
    msg.add_alternative(
        '<!DOCTYPE html><html><body style="font-family:Segoe UI,Arial,sans-serif;color:#333;'
        'line-height:1.6;max-width:600px;margin:auto;padding:20px">'
        f'<h2 style="color:#0f766e">{safe_brand}</h2><div>{safe}</div>'
        '<hr style="border:0;border-top:1px solid #eee">'
        f'<p style="font-size:13px;color:#777">Regards,<br>{safe_brand}</p>'
        '</body></html>',
        subtype='html',
    )
    return msg


class _SendTask(Task):
    autoretry_for = _RETRYABLE
    retry_backoff = True
    retry_backoff_max = 300
    retry_jitter = True
    max_retries = 3


def _smtp_send(msg: EmailMessage) -> None:
    settings = get_settings()
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        if settings.smtp_use_tls:
            smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        # Mailpit and many local SMTPs have no AUTH; only login when both are set.
        if settings.smtp_user.strip() and settings.smtp_password:
            smtp.login(settings.smtp_user, settings.smtp_password)
        refused = smtp.send_message(msg)
    if refused:
        log.error('email_recipient_refused', recipients=sorted(refused), subject=msg['Subject'])
        raise smtplib.SMTPRecipientsRefused(refused)


def _deliver(to: str, subject: str, body: str) -> None:
    """Console backend in dev, real SMTP otherwise."""
    settings = get_settings()
    if settings.email_backend == 'console':
        print(
            f'\n===== DEV EMAIL =====\nTo: {to}\nSubject: {subject}\n\n{body}\n=====================\n',
            flush=True,
        )
        log.info('email_printed_to_console', to=to, subject=subject)
        return
    _smtp_send(_render_generic(to, subject, body))


def _deliver_otp(
    to: str, subject: str, *, display_name: str, otp: str, purpose: str, minutes: int
) -> None:
    settings = get_settings()
    if settings.email_backend == 'console':
        body = _otp_plain(display_name, otp, purpose, minutes)
        print(
            f'\n===== DEV EMAIL =====\nTo: {to}\nSubject: {subject}\n\n{body}\n=====================\n',
            flush=True,
        )
        log.info('email_printed_to_console', to=to, subject=subject)
        return
    _smtp_send(
        _render_otp(to, subject, display_name=display_name, otp=otp, purpose=purpose, minutes=minutes)
    )


@celery_app.task(base=_SendTask, name='app.tasks.email.send_email')
def send_email(to: str, subject: str, body: str) -> None:
    _deliver(to, subject, body)
    log.info('email_sent', to=to, subject=subject)


@celery_app.task(base=_SendTask, name='app.tasks.email.send_otp_email', bind=True)
def send_otp_email(
    self: Task,  # noqa: ARG001  (bind=True is what gives this task its retry policy)
    *,
    to: str,
    subject: str,
    body: str,
    purpose: str,
    session_id: str,
    display_name: str,
) -> None:
    """Send an OTP mail, unsealing the code only inside this process."""
    settings = get_settings()
    try:
        otp = decrypt_otp_payload(body, session_id=session_id, purpose=purpose)
    except Exception as exc:
        log.error('otp_payload_undecryptable', error=type(exc).__name__, purpose=purpose)
        return

    minutes = max(round(settings.otp_ttl_seconds / 60), 1)
    # Delivered inline: chaining send_email from a worker would occupy two slots
    # per OTP and can deadlock the queue under load.
    _deliver_otp(
        to, subject, display_name=display_name, otp=otp, purpose=purpose, minutes=minutes
    )
    log.info('otp_email_sent', to=to, purpose=purpose)
