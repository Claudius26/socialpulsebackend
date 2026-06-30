"""
Email OTP — issue + send branded verification codes for CardPulse.
"""
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone

from .models import EmailOTP

logger = logging.getLogger(__name__)

OTP_TTL_MINUTES = 10
RESEND_COOLDOWN_SECONDS = 60


def generate_code() -> str:
    return f"{secrets.randbelow(900000) + 100000}"  # always 6 digits


def issue_otp(user, purpose=EmailOTP.PURPOSE_VERIFY) -> str:
    """Create a fresh OTP for the user, invalidating older unused ones."""
    EmailOTP.objects.filter(user=user, purpose=purpose, used=False).update(used=True)
    code = generate_code()
    otp = EmailOTP(
        user=user, purpose=purpose,
        expires_at=timezone.now() + timedelta(minutes=OTP_TTL_MINUTES),
    )
    otp.set_code(code)
    otp.save()
    return code


def _html(code, name) -> str:
    return f"""\
<!DOCTYPE html>
<html>
  <body style="margin:0;padding:0;background:#0B1220;font-family:Arial,Helvetica,sans-serif;">
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#0B1220;padding:32px 0;">
      <tr><td align="center">
        <table width="480" cellpadding="0" cellspacing="0"
               style="background:#111A2E;border:1px solid #22304D;border-radius:18px;overflow:hidden;">
          <tr><td style="background:linear-gradient(135deg,#4F46E5,#6366F1);padding:24px 32px;">
            <div style="color:#fff;font-size:22px;font-weight:800;letter-spacing:0.5px;">CardPulse</div>
            <div style="color:#C7D2FE;font-size:13px;margin-top:2px;">Verify your email</div>
          </td></tr>
          <tr><td style="padding:32px;">
            <p style="color:#F8FAFC;font-size:16px;margin:0 0 8px;">Hi {name or "there"},</p>
            <p style="color:#94A3B8;font-size:14px;line-height:22px;margin:0 0 24px;">
              Welcome to CardPulse! Use the code below to verify your email and start
              trading giftcards, sending to friends, and cashing out.
            </p>
            <div style="background:#16233D;border:1px solid #22304D;border-radius:12px;
                        padding:18px;text-align:center;margin-bottom:24px;">
              <div style="color:#F8FAFC;font-size:34px;font-weight:800;letter-spacing:10px;">{code}</div>
            </div>
            <p style="color:#94A3B8;font-size:13px;margin:0;">
              This code expires in {OTP_TTL_MINUTES} minutes. If you didn't sign up for
              CardPulse, you can safely ignore this email.
            </p>
          </td></tr>
          <tr><td style="padding:16px 32px;border-top:1px solid #22304D;">
            <div style="color:#475569;font-size:12px;">© CardPulse · Secure · Fast · Reliable</div>
          </td></tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>"""


def send_otp_email(user, code) -> bool:
    subject = "Your CardPulse verification code"
    text = (
        f"Hi {user.first_name or user.full_name or 'there'},\n\n"
        f"Your CardPulse verification code is: {code}\n"
        f"It expires in {OTP_TTL_MINUTES} minutes.\n\n"
        f"If you didn't sign up for CardPulse, ignore this email."
    )
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None) or "CardPulse <no-reply@cardpulse.org>"
    try:
        msg = EmailMultiAlternatives(subject, text, from_email, [user.email])
        msg.attach_alternative(_html(code, user.first_name or user.full_name), "text/html")
        msg.send()
        return True
    except Exception as exc:  # don't fail registration if SMTP hiccups
        logger.warning("OTP email send failed for %s: %s", user.email, exc)
        return False


def issue_and_send(user, purpose=EmailOTP.PURPOSE_VERIFY) -> bool:
    code = issue_otp(user, purpose)
    return send_otp_email(user, code)
