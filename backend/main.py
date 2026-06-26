"""
NorthStar FastAPI Backend
Flow: Contact Form → Validate → Rate-limit → (optional hCaptcha) → SMTP → Admin Inbox
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from typing import Optional
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import os, re, smtplib, logging, httpx, html
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv(override=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("northstar")

app = FastAPI(
    title="NorthStar API",
    description="Contact enquiry backend for NorthStar Financial Services",
    version="2.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# ─────────────────────────────────────────────
# CORS
# ─────────────────────────────────────────────
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["Content-Type", "X-Captcha-Token"],
)

# ─────────────────────────────────────────────
# Config (from .env)
# ─────────────────────────────────────────────
ADMIN_EMAIL   = os.getenv("ADMIN_EMAIL",  "workwithnorthstar@gmail.com")
SMTP_HOST     = os.getenv("SMTP_HOST",    "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER",    "")
SMTP_PASS     = os.getenv("SMTP_PASS",    "")

# hCaptcha (optional — set HCAPTCHA_SECRET and HCAPTCHA_SITEKEY in .env to enable)
HCAPTCHA_SECRET  = os.getenv("HCAPTCHA_SECRET", "")
HCAPTCHA_SITEKEY = os.getenv("HCAPTCHA_SITEKEY", "")
HCAPTCHA_VERIFY  = "https://hcaptcha.com/siteverify"


# Rate limiting: max N requests per IP per window
RATE_LIMIT_MAX    = int(os.getenv("RATE_LIMIT_MAX",    "5"))    # 5 submits
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "3600")) # per hour

# ─────────────────────────────────────────────
# In-memory rate-limit store  {ip: [timestamps]}
# ─────────────────────────────────────────────
_rate_store: dict[str, list[datetime]] = defaultdict(list)


def check_rate_limit(ip: str) -> None:
    """Raise 429 if this IP has exceeded RATE_LIMIT_MAX in the window."""
    now    = datetime.now(timezone.utc)
    window = now - timedelta(seconds=RATE_LIMIT_WINDOW)
    hits   = _rate_store[ip]

    # Purge old entries
    _rate_store[ip] = [t for t in hits if t > window]

    if len(_rate_store[ip]) >= RATE_LIMIT_MAX:
        logger.warning("Rate limit hit for IP %s", ip)
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please wait a while and try again.",
        )

    _rate_store[ip].append(now)


async def verify_captcha(token: str) -> bool:
    """Verify hCaptcha token. Returns True if valid or if CAPTCHA is not configured."""
    if not HCAPTCHA_SECRET:
        return True  # CAPTCHA not configured — skip
    if not token:
        return False # CAPTCHA configured but no token provided
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                HCAPTCHA_VERIFY,
                data={"secret": HCAPTCHA_SECRET, "response": token},
            )
            data = resp.json()
            return data.get("success", False)
    except Exception as exc:
        logger.error("CAPTCHA verification failed: %s", exc)
        return False  # Fail open would be bad — reject on error


# ─────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────
class EnquiryRequest(BaseModel):
    name:           str
    phone:          str
    email:          Optional[str] = None
    service:        str
    message:        Optional[str] = None
    captcha_token:  Optional[str] = None   # hCaptcha response token

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2:
            raise ValueError("Name must be at least 2 characters.")
        if len(v) > 100:
            raise ValueError("Name must not exceed 100 characters.")
        if not re.match(r"^[A-Za-z\s\.\-']+$", v):
            raise ValueError("Name contains invalid characters.")
        return v

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        v = re.sub(r"[\s\-\(\)]", "", v.strip())
        if not re.match(r"^[6-9]\d{9}$", v):
            raise ValueError("Please enter a valid 10-digit Indian mobile number.")
        return v

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: Optional[str]) -> Optional[str]:
        if v and v.strip():
            v = v.strip().lower()
            if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", v):
                raise ValueError("Please enter a valid email address.")
            if len(v) > 254:
                raise ValueError("Email address is too long.")
            return v
        return None

    @field_validator("service")
    @classmethod
    def validate_service(cls, v: str) -> str:
        v = v.strip()
        allowed = {
            "Individual ITR Filing", "Business ITR Filing",
            "GST Registration", "GST Filing",
            "Tax Planning & Consultation", "Insurance",
            "TDS Filing", "Tax Notice Assistance", "Other",
        }
        if v not in allowed:
            raise ValueError("Please select a valid service.")
        return v

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: Optional[str]) -> Optional[str]:
        if v:
            v = v.strip()
            if len(v) > 1000:
                raise ValueError("Message must not exceed 1000 characters.")
            return v
        return v


class ContactResponse(BaseModel):
    success:     bool
    message:     str
    enquiry_id:  Optional[str] = None


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def _generate_id() -> str:
    from uuid import uuid4
    return f"ENQ-{str(uuid4())[:8].upper()}"


def _build_html_email(e: dict) -> str:
    """Build a styled HTML email body for the admin notification."""
    safe_enquiry_id = html.escape(e["enquiry_id"])
    safe_name       = html.escape(e["name"])
    safe_phone      = html.escape(e["phone"])
    safe_email      = html.escape(e.get("email") or "")
    safe_service    = html.escape(e["service"])
    safe_message    = html.escape(e.get("message") or "—").replace("\n", "<br/>")
    safe_created_at = html.escape(e["created_at"])

    rows = [
        ("Enquiry ID",  safe_enquiry_id),
        ("Name",        safe_name),
        ("Phone",       safe_phone),
        ("Email",       safe_email or "—"),
        ("Service",     safe_service),
        ("Message",     safe_message),
        ("Received At", safe_created_at),
    ]
    table_rows = "".join(
        f"""<tr>
              <td style="padding:8px 14px;font-weight:600;color:#1B2A47;
                         border-bottom:1px solid #E7E3DC;width:130px;
                         font-size:13px;">{label}</td>
              <td style="padding:8px 14px;color:#58606B;border-bottom:1px solid #E7E3DC;
                         font-size:13px;">{value}</td>
            </tr>"""
        for label, value in rows
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"/></head>
<body style="margin:0;padding:0;background:#FAF9F6;font-family:'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0"
         style="background:#FAF9F6;padding:40px 20px;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0"
             style="background:#ffffff;border-radius:12px;
                    box-shadow:0 4px 20px rgba(27,42,71,0.08);
                    border:1px solid #E7E3DC;overflow:hidden;max-width:600px;">
        <!-- Header -->
        <tr>
          <td style="background:linear-gradient(135deg,#1B2A47,#121C30);
                     padding:28px 32px;text-align:center;">
            <p style="margin:0;font-size:22px;font-weight:700;color:#ffffff;
                      letter-spacing:-0.3px;">NorthStar</p>
            <p style="margin:6px 0 0;font-size:13px;color:rgba(255,255,255,0.7);">
              New Contact Enquiry</p>
          </td>
        </tr>
        <!-- Body -->
        <tr>
          <td style="padding:28px 32px;">
            <p style="margin:0 0 20px;font-size:15px;color:#1C2024;">
              A new enquiry has been submitted through the NorthStar website.
              Please follow up within <strong>24 hours</strong>.
            </p>
            <table width="100%" cellpadding="0" cellspacing="0"
                   style="border:1px solid #E7E3DC;border-radius:8px;overflow:hidden;">
              {table_rows}
            </table>
          </td>
        </tr>
        <!-- CTA -->
        <tr>
          <td style="padding:0 32px 28px;">
            <table cellpadding="0" cellspacing="0">
              <tr>
                <td style="background:#1B2A47;border-radius:8px;padding:12px 24px;">
                  <a href="tel:+91{safe_phone}"
                     style="color:#ffffff;text-decoration:none;font-size:14px;
                            font-weight:600;">📞 Call {safe_name}</a>
                </td>
                {('<td style="width:12px;"></td><td style="background:#1F563B;border-radius:8px;padding:12px 24px;"><a href="mailto:' + safe_email + '" style="color:#ffffff;text-decoration:none;font-size:14px;font-weight:600;">✉ Reply via Email</a></td>') if safe_email else ""}
              </tr>
            </table>
          </td>
        </tr>
        <!-- Footer -->
        <tr>
          <td style="background:#F5F3EF;padding:16px 32px;text-align:center;
                     border-top:1px solid #E7E3DC;">
            <p style="margin:0;font-size:12px;color:#8D96A3;">
              NorthStar Financial Services · Pondicherry, India<br/>
              This is an automated notification. Do not reply to this email.
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""



def _build_user_html_email(e: dict) -> str:
    """Build styled HTML confirmation email for the user."""
    safe_name    = html.escape(e["name"])
    safe_service = html.escape(e["service"])
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"/></head>
<body style="margin:0;padding:0;background:#FAF9F6;font-family:'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0"
         style="background:#FAF9F6;padding:40px 20px;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0"
             style="background:#ffffff;border-radius:12px;
                    box-shadow:0 4px 20px rgba(27,42,71,0.08);
                    border:1px solid #E7E3DC;overflow:hidden;max-width:600px;">
        <!-- Header -->
        <tr>
          <td style="background:linear-gradient(135deg,#1B2A47,#121C30);
                     padding:28px 32px;text-align:center;">
            <p style="margin:0;font-size:22px;font-weight:700;color:#ffffff;
                      letter-spacing:-0.3px;">NorthStar</p>
            <p style="margin:6px 0 0;font-size:13px;color:rgba(255,255,255,0.7);">
              Enquiry Received</p>
          </td>
        </tr>
        <!-- Body -->
        <tr>
          <td style="padding:28px 32px;line-height:1.6;color:#333F4E;font-size:15px;">
            <p style="margin:0 0 16px;font-weight:600;font-size:16px;color:#1B2A47;">
              Hello {safe_name},
            </p>
            <p style="margin:0 0 16px;">
              Thank you for contacting NorthStar Financial Services. We have received your enquiry regarding <strong>{safe_service}</strong>.
            </p>
            <p style="margin:0 0 16px;">
              Our expert advisors are currently reviewing your details and will get back to you within <strong>24 hours</strong> with the next steps.
            </p>
            <p style="margin:0 0 24px;">
              If you have any urgent queries, feel free to connect with us directly on WhatsApp at <a href="https://wa.me/919486409362" style="color:#1B2A47;font-weight:600;text-decoration:none;">+91 94864 09362</a>.
            </p>
            <p style="margin:0;font-size:14px;color:#58606B;">
              Best regards,<br/>
              <strong>NorthStar Team</strong>
            </p>
          </td>
        </tr>
        <!-- Footer -->
        <tr>
          <td style="background:#F5F3EF;padding:16px 32px;text-align:center;
                     border-top:1px solid #E7E3DC;">
            <p style="margin:0;font-size:12px;color:#8D96A3;">
              NorthStar Financial Services · Pondicherry, India<br/>
              <span style="font-style:italic;">Disclaimer: This is an auto-generated email confirmation. Please do not reply directly to this message.</span>
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def send_admin_email(enquiry: dict) -> None:
    """Send admin notification email and user confirmation email in one SMTP session."""
    if not SMTP_USER or not SMTP_PASS:
        logger.info("SMTP not configured — skipping email for %s", enquiry["enquiry_id"])
        return

    try:
        # 1. Construct Admin Notification Message
        admin_msg = MIMEMultipart("alternative")
        admin_msg["Subject"] = (
            f"[NorthStar] New Enquiry {enquiry['enquiry_id']} — {enquiry['service']}"
        )
        admin_msg["From"]    = f"NorthStar Contact <{SMTP_USER}>"
        admin_msg["To"]      = ADMIN_EMAIL
        admin_msg["Reply-To"] = enquiry.get("email") or SMTP_USER

        admin_plain = (
            f"New enquiry from {enquiry['name']}\n\n"
            f"ID      : {enquiry['enquiry_id']}\n"
            f"Phone   : {enquiry['phone']}\n"
            f"Email   : {enquiry.get('email') or '—'}\n"
            f"Service : {enquiry['service']}\n"
            f"Message : {enquiry.get('message') or '—'}\n"
            f"Time    : {enquiry['created_at']}\n"
        )
        admin_msg.attach(MIMEText(admin_plain, "plain", "utf-8"))
        admin_msg.attach(MIMEText(_build_html_email(enquiry), "html", "utf-8"))

        # 2. Construct User Confirmation Message (if email is provided)
        user_msg = None
        user_email = enquiry.get("email")
        if user_email:
            user_msg = MIMEMultipart("alternative")
            user_msg["Subject"] = f"[NorthStar] We have received your enquiry — {enquiry['enquiry_id']}"
            user_msg["From"]    = f"NorthStar Financial Services <{SMTP_USER}>"
            user_msg["To"]      = user_email
            user_msg["Reply-To"] = SMTP_USER

            user_plain = (
                f"Hello {enquiry['name']},\n\n"
                f"Thank you for contacting NorthStar. We have received your enquiry regarding '{enquiry['service']}'.\n"
                f"Our team is currently reviewing your details and will get back to you within 24 hours.\n\n"
                f"For urgent queries, feel free to reach us via WhatsApp at +91 94864 09362.\n\n"
                f"Best regards,\n"
                f"NorthStar Team\n\n"
                f"---\n"
                f"Disclaimer: This is an auto-generated email confirmation. Please do not reply directly to this message.\n"
            )
            user_msg.attach(MIMEText(user_plain, "plain", "utf-8"))
            user_msg.attach(MIMEText(_build_user_html_email(enquiry), "html", "utf-8"))

        # 3. SMTP Session
        if SMTP_PORT == 465:
            smtp_client = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT)
        else:
            smtp_client = smtplib.SMTP(SMTP_HOST, SMTP_PORT)

        with smtp_client as smtp:
            if SMTP_PORT != 465:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
            smtp.login(SMTP_USER, SMTP_PASS)
            
            # Send to admin
            smtp.send_message(admin_msg)
            logger.info("Admin email sent for %s", enquiry["enquiry_id"])
            
            # Send to user (if email is provided)
            if user_msg:
                smtp.send_message(user_msg)
                logger.info("User confirmation email sent to %s for %s", user_email, enquiry["enquiry_id"])

    except smtplib.SMTPAuthenticationError:
        logger.error("SMTP auth failed — check SMTP_USER/SMTP_PASS in .env")
    except smtplib.SMTPException as exc:
        logger.error("SMTP error for %s: %s", enquiry["enquiry_id"], exc)
    except Exception as exc:
        logger.error("Unexpected email error: %s", exc)


# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────
@app.get("/api/health", tags=["System"])
def health():
    """Health check."""
    return {
        "status":  "ok",
        "service": "NorthStar API",
        "version": "2.0.0",
        "smtp_configured": bool(SMTP_USER and SMTP_PASS),
        "captcha_enabled": bool(HCAPTCHA_SECRET and HCAPTCHA_SITEKEY),
        "captcha_sitekey": HCAPTCHA_SITEKEY,
    }


@app.post("/api/enquiry", response_model=ContactResponse, tags=["Enquiry"])
async def submit_enquiry(
    payload:          EnquiryRequest,
    request:          Request,
    background_tasks: BackgroundTasks,
):
    """
    Full pipeline:
      1. Extract client IP
      2. Check rate limit (max 5 / hour per IP)
      3. Verify hCaptcha token (if HCAPTCHA_SECRET is set)
      4. Validate + sanitise input (Pydantic)
      5. Store enquiry record in memory
      6. Dispatch styled HTML email to admin via SMTP (background)
      7. Return success
    """
    # ── 1. Get real client IP ────────────────
    forwarded = request.headers.get("X-Forwarded-For")
    client_ip = forwarded.split(",")[0].strip() if forwarded else (
        request.client.host if request.client else "unknown"
    )

    # ── 2. Rate limit ────────────────────────
    check_rate_limit(client_ip)

    # ── 3. CAPTCHA ───────────────────────────
    token = payload.captcha_token or request.headers.get("X-Captcha-Token", "")
    if HCAPTCHA_SECRET and not await verify_captcha(token):
        raise HTTPException(status_code=400, detail="CAPTCHA verification failed. Please try again.")

    # ── 4. Build record ──────────────────────
    enquiry_id = _generate_id()
    record = {
        "enquiry_id": enquiry_id,
        "name":       payload.name,
        "phone":      payload.phone,
        "email":      payload.email,
        "service":    payload.service,
        "message":    payload.message,
        "created_at": datetime.now(timezone.utc).strftime("%d %b %Y, %I:%M %p UTC"),
        "ip":         client_ip,
        "status":     "new",
    }

    logger.info("Enquiry %s received from %s — service: %s", enquiry_id, client_ip, payload.service)

    # ── 5. Send email in background ──────────
    background_tasks.add_task(send_admin_email, record)

    # ── 6. Respond ───────────────────────────
    return ContactResponse(
        success=True,
        message="Thank you! Your enquiry has been received. We'll get back to you within 24 hours.",
        enquiry_id=enquiry_id,
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException

    # 1. Validation error (Pydantic / input validation)
    if isinstance(exc, RequestValidationError):
        logger.warning("Validation error on %s: %s", request.url.path, exc.errors())
        errs = exc.errors()
        msg = "Validation failed."
        if errs:
            msg = errs[0].get("msg", "Invalid input value.")
            if msg.startswith("Value error, "):
                msg = msg.replace("Value error, ", "")
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": msg, "detail": msg},
        )

    # 2. HTTP Exception (Rate limit / Bad request)
    if isinstance(exc, StarletteHTTPException):
        logger.warning("HTTP error on %s: status %s — %s", request.url.path, exc.status_code, exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"success": False, "message": exc.detail, "detail": exc.detail},
        )

    # 3. All other unexpected errors
    logger.error("Unhandled error on %s: %s", request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"success": False, "message": "An internal error occurred. Please try again."},
    )


# ─────────────────────────────────────────────
# Serve frontend static files
# ─────────────────────────────────────────────
FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
