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
import os, re, logging, httpx, html
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

# Primary email provider preference: "resend" or "sendgrid" (defaults to "resend")
PRIMARY_PROVIDER = os.getenv("PRIMARY_PROVIDER", "resend").strip().lower()

# HTTP API keys for Resend and SendGrid
RESEND_API_KEY   = os.getenv("RESEND_API_KEY", "")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")

# Optional custom sender email linked to your verified domain (e.g. "info@yourdomain.com")
SENDER_EMAIL     = os.getenv("SENDER_EMAIL", "").strip()

# hCaptcha (optional — set HCAPTCHA_SECRET and HCAPTCHA_SITEKEY in .env to enable)
HCAPTCHA_SECRET  = os.getenv("HCAPTCHA_SECRET", "")
HCAPTCHA_SITEKEY = os.getenv("HCAPTCHA_SITEKEY", "")
HCAPTCHA_VERIFY  = "https://hcaptcha.com/siteverify"

# Google Sheets Web App URL for Customer Feedback Integration
GOOGLE_SHEET_WEBAPP_URL = os.getenv("GOOGLE_SHEET_WEBAPP_URL", "").strip().strip("'\"")



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


class FeedbackRequest(BaseModel):
    name:           str
    role:           Optional[str] = None
    rating:         int
    message:        str
    captcha_token:  Optional[str] = None

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

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: Optional[str]) -> Optional[str]:
        if v:
            v = v.strip()
            if len(v) > 100:
                raise ValueError("Role or location must not exceed 100 characters.")
            if not re.match(r"^[A-Za-z0-9\s\.\,\-\'\/]+$", v):
                raise ValueError("Role or location contains invalid characters.")
            return v
        return None

    @field_validator("rating")
    @classmethod
    def validate_rating(cls, v: int) -> int:
        if v < 1 or v > 5:
            raise ValueError("Rating must be an integer between 1 and 5.")
        return v

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 10:
            raise ValueError("Feedback message must be at least 10 characters.")
        if len(v) > 1000:
            raise ValueError("Feedback message must not exceed 1000 characters.")
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


def send_via_resend(enquiry: dict) -> bool:
    """Send admin notification email and user confirmation email via Resend API.
    Returns True if successfully sent, False otherwise.
    """
    if not RESEND_API_KEY:
        logger.warning("Resend API key is missing — skipping email for %s", enquiry["enquiry_id"])
        return False

    # Use custom sender domain mail or fallback to admin mail
    raw_from = SENDER_EMAIL or ADMIN_EMAIL
    from_email = f"NorthStar Contact <{raw_from}>" if "<" not in raw_from else raw_from

    try:
        headers = {
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        }

        # 1. Admin Email
        admin_payload = {
            "from": from_email,
            "to": [ADMIN_EMAIL],
            "subject": f"[NorthStar] New Enquiry {enquiry['enquiry_id']} — {enquiry['service']}",
            "html": _build_html_email(enquiry),
        }
        if enquiry.get("email"):
            admin_payload["reply_to"] = enquiry["email"]

        with httpx.Client(timeout=10) as client:
            resp = client.post("https://api.resend.com/emails", json=admin_payload, headers=headers)
            if resp.status_code >= 400:
                logger.error("Resend API error sending admin email: %s", resp.text)
                return False
            
            logger.info("Admin email sent via Resend for %s", enquiry["enquiry_id"])

            # 2. User Confirmation Email
            user_email = enquiry.get("email")
            if user_email:
                user_payload = {
                    "from": f"NorthStar Financial Services <{raw_from}>" if "<" not in raw_from else raw_from,
                    "to": [user_email],
                    "subject": f"[NorthStar] We have received your enquiry — {enquiry['enquiry_id']}",
                    "html": _build_user_html_email(enquiry),
                    "reply_to": ADMIN_EMAIL,
                }
                resp_user = client.post("https://api.resend.com/emails", json=user_payload, headers=headers)
                if resp_user.status_code >= 400:
                    logger.warning("Resend user confirmation email failed (expected if recipient email is unverified on Resend sandbox): %s", resp_user.text)
                else:
                    logger.info("User confirmation email sent to %s for %s", user_email, enquiry["enquiry_id"])
            return True

    except Exception as exc:
        logger.error("Error sending via Resend: %s", exc)
        return False


def send_via_sendgrid(enquiry: dict) -> bool:
    """Send admin notification email and user confirmation email via SendGrid API.
    Returns True if successfully sent, False otherwise.
    """
    if not SENDGRID_API_KEY:
        logger.warning("SendGrid API key is missing — skipping email for %s", enquiry["enquiry_id"])
        return False

    # SendGrid sender must be verified in the account
    raw_from = SENDER_EMAIL or ADMIN_EMAIL
    from_email = raw_from

    try:
        headers = {
            "Authorization": f"Bearer {SENDGRID_API_KEY}",
            "Content-Type": "application/json",
        }

        # 1. Admin Email
        admin_payload = {
            "personalizations": [{
                "to": [{"email": ADMIN_EMAIL}]
            }],
            "from": {
                "email": from_email,
                "name": "NorthStar Contact"
            },
            "subject": f"[NorthStar] New Enquiry {enquiry['enquiry_id']} — {enquiry['service']}",
            "content": [{
                "type": "text/html",
                "value": _build_html_email(enquiry)
            }]
        }
        if enquiry.get("email"):
            admin_payload["reply_to"] = {"email": enquiry["email"]}

        with httpx.Client(timeout=10) as client:
            resp = client.post("https://api.sendgrid.com/v3/mail/send", json=admin_payload, headers=headers)
            if resp.status_code >= 400:
                logger.error("SendGrid API error sending admin email: %s", resp.text)
                return False
            
            logger.info("Admin email sent via SendGrid for %s", enquiry["enquiry_id"])

            # 2. User Confirmation Email
            user_email = enquiry.get("email")
            if user_email:
                user_payload = {
                    "personalizations": [{
                        "to": [{"email": user_email}]
                    }],
                    "from": {
                        "email": from_email,
                        "name": "NorthStar Financial Services"
                    },
                    "subject": f"[NorthStar] We have received your enquiry — {enquiry['enquiry_id']}",
                    "content": [{
                        "type": "text/html",
                        "value": _build_user_html_email(enquiry)
                    }],
                    "reply_to": {"email": from_email}
                }
                resp_user = client.post("https://api.sendgrid.com/v3/mail/send", json=user_payload, headers=headers)
                if resp_user.status_code >= 400:
                    logger.error("SendGrid API error sending user confirmation email: %s", resp_user.text)
                else:
                    logger.info("User confirmation email sent via SendGrid to %s for %s", user_email, enquiry["enquiry_id"])
            return True

    except Exception as exc:
        logger.error("Error sending via SendGrid: %s", exc)
        return False


def send_admin_email(enquiry: dict) -> None:
    """Send email notifications via Resend or SendGrid with automatic failover."""
    # Define primary and backup based on configuration
    if PRIMARY_PROVIDER == "sendgrid":
        providers = [
            ("SendGrid", send_via_sendgrid),
            ("Resend", send_via_resend)
        ]
    else:
        providers = [
            ("Resend", send_via_resend),
            ("SendGrid", send_via_sendgrid)
        ]

    # Try each configured provider in order until one succeeds
    sent_successfully = False
    for name, send_func in providers:
        try:
            logger.info("Attempting to dispatch email via %s...", name)
            if send_func(enquiry):
                logger.info("Successfully dispatched email using %s.", name)
                sent_successfully = True
                break
            else:
                logger.warning("%s dispatch failed (limit reached, error, or not configured). Trying backup...", name)
        except Exception as e:
            logger.error("Error during %s dispatch: %s. Trying backup...", name, e)

    if not sent_successfully:
        logger.critical("All email providers failed! Enquiry notification %s was NOT sent.", enquiry["enquiry_id"])


# ─────────────────────────────────────────────
# Testimonials & Feedback Cache / Fallback System
# ─────────────────────────────────────────────
DEFAULT_TESTIMONIALS = [
    {
        "name": "Rajesh Kumar",
        "role": "Salaried Professional, Mumbai",
        "rating": 5,
        "message": "Filed my ITR within a day. The team explained every deduction clearly. Highly recommended for anyone who finds taxes confusing."
    },
    {
        "name": "Priya Sharma",
        "role": "Freelancer, Bangalore",
        "rating": 5,
        "message": "I had multiple income sources and was worried about filing errors. NorthStar handled everything perfectly and even helped me save more tax than I expected."
    },
    {
        "name": "Anil Mehta",
        "role": "Small Business Owner, Delhi",
        "rating": 5,
        "message": "GST filing used to be a monthly headache. Now I just share my documents and they handle the rest. Very professional and always on time."
    },
    {
        "name": "Sunita Joshi",
        "role": "Business Owner, Pune",
        "rating": 5,
        "message": "I received a notice from the IT department and was panicking. NorthStar resolved it within 48 hours. Their expertise is unmatched."
    },
    {
        "name": "Vikram Nair",
        "role": "IT Professional, Hyderabad",
        "rating": 4,
        "message": "Best tax filing experience. The dashboard makes it so easy to track the status. Refund came in 3 weeks. Will definitely use again!"
    },
    {
        "name": "Neha Verma",
        "role": "First-time Taxpayer, Chennai",
        "rating": 5,
        "message": "As a first-time taxpayer I was completely lost. The team guided me step by step. Super patient and professional. Highly recommended!"
    }
]

CACHE_FILE = os.path.join(os.path.dirname(__file__), "feedback_cache.json")
_feedback_cache = {"last_updated": None, "data": DEFAULT_TESTIMONIALS}


def load_local_cache():
    global _feedback_cache
    if os.path.exists(CACHE_FILE):
        try:
            import json
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
                if isinstance(cached_data, list):
                    _feedback_cache["data"] = cached_data
                elif isinstance(cached_data, dict) and "data" in cached_data:
                    _feedback_cache = cached_data
                logger.info("Loaded feedback cache from file with %d items.", len(_feedback_cache["data"]))
        except Exception as e:
            logger.error("Failed to load local feedback cache file: %s", e)


async def refresh_feedback_cache():
    global _feedback_cache
    if not GOOGLE_SHEET_WEBAPP_URL:
        return
    
    try:
        logger.info("Fetching fresh feedback from Google Sheet Web App...")
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(GOOGLE_SHEET_WEBAPP_URL)
            if resp.status_code == 200:
                resp_json = resp.json()
                if isinstance(resp_json, list):
                    valid_feedbacks = []
                    for fb in resp_json:
                        if isinstance(fb, dict) and "name" in fb and "rating" in fb and "message" in fb:
                            valid_feedbacks.append({
                                "name": html.escape(str(fb["name"])),
                                "role": html.escape(str(fb.get("role", ""))),
                                "rating": min(5, max(1, int(fb["rating"]))),
                                "message": html.escape(str(fb["message"]))
                            })
                    
                    if valid_feedbacks:
                        _feedback_cache["data"] = valid_feedbacks
                        _feedback_cache["last_updated"] = datetime.now(timezone.utc).isoformat()
                        
                        import json
                        with open(CACHE_FILE, "w", encoding="utf-8") as f:
                            json.dump(_feedback_cache, f, ensure_ascii=False, indent=2)
                        logger.info("Feedback cache successfully updated from Google Sheet. Items: %d", len(valid_feedbacks))
                    else:
                        logger.warning("Google Sheet returned no valid feedback entries.")
                else:
                    logger.error("Google Sheet response was not a JSON list: %s", resp_json)
            else:
                logger.error("Failed to fetch from Google Sheet. Status: %d", resp.status_code)
    except Exception as e:
        logger.error("Error refreshing feedback cache: %s", e)


@app.on_event("startup")
async def startup_event():
    load_local_cache()
    if GOOGLE_SHEET_WEBAPP_URL:
        import asyncio
        asyncio.create_task(refresh_feedback_cache())


# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────

@app.get("/api/feedback", tags=["Feedback"])
async def get_feedback(background_tasks: BackgroundTasks):
    """
    Get all approved testimonials.
    If the cache is empty or older than 10 minutes, triggers a background refresh.
    """
    global _feedback_cache
    
    cache_needs_refresh = False
    if GOOGLE_SHEET_WEBAPP_URL:
        if not _feedback_cache.get("last_updated"):
            cache_needs_refresh = True
        else:
            try:
                last_updated = datetime.fromisoformat(_feedback_cache["last_updated"])
                now = datetime.now(timezone.utc)
                if (now - last_updated).total_seconds() > 600: # 10 minutes
                    cache_needs_refresh = True
            except Exception:
                cache_needs_refresh = True
                
    if cache_needs_refresh:
        logger.info("Triggering background feedback cache refresh...")
        background_tasks.add_task(refresh_feedback_cache)
        
    return _feedback_cache["data"]


@app.post("/api/feedback", tags=["Feedback"])
async def submit_feedback(
    payload: FeedbackRequest,
    request: Request,
):
    """
    Submit feedback to Google Apps Script.
    """

    # -----------------------------
    # 1. Rate limiting
    # -----------------------------
    forwarded = request.headers.get("X-Forwarded-For")
    client_ip = (
        forwarded.split(",")[0].strip()
        if forwarded
        else (request.client.host if request.client else "unknown")
    )

    check_rate_limit(client_ip)

    # -----------------------------
    # 2. Check URL
    # -----------------------------
    if not GOOGLE_SHEET_WEBAPP_URL:
        logger.error("GOOGLE_SHEET_WEBAPP_URL is NOT configured!")
        raise HTTPException(
            status_code=500,
            detail="Google Sheet Web App URL is missing."
        )

    logger.info("=" * 70)
    logger.info("Starting feedback submission")
    logger.info("Google Apps Script URL: %s", GOOGLE_SHEET_WEBAPP_URL)

    # -----------------------------
    # 3. Payload
    # -----------------------------
    post_data = {
        "name": payload.name,
        "role": payload.role or "",
        "rating": payload.rating,
        "message": payload.message,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    logger.info("Payload:")
    logger.info(post_data)

    try:
        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=True
        ) as client:

            logger.info("Sending POST request...")

            # Send as form data (recommended for Apps Script)
            response = await client.post(
                GOOGLE_SHEET_WEBAPP_URL,
                data=post_data
            )

            logger.info("=" * 40)
            logger.info("RESPONSE RECEIVED")
            logger.info("Status Code: %s", response.status_code)
            logger.info("Final URL: %s", response.url)
            logger.info("Headers: %s", dict(response.headers))
            logger.info("Body:\n%s", response.text)
            logger.info("=" * 40)

            if response.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"Google returned {response.status_code}"
                )

            return {
                "success": True,
                "message": "Feedback submitted successfully."
            }

    except httpx.RequestError as e:
        logger.exception("HTTPX Request Error")
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

    except Exception as e:
        logger.exception("Unexpected Error")
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )
@app.get("/api/health", tags=["System"])
def health():
    """Health check."""
    return {
        "status":  "ok",
        "service": "NorthStar API",
        "version": "2.0.0",
        "primary_provider": PRIMARY_PROVIDER,
        "resend_configured": bool(RESEND_API_KEY),
        "sendgrid_configured": bool(SENDGRID_API_KEY),
        "email_configured": bool(RESEND_API_KEY or SENDGRID_API_KEY),
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


# Prevent browser caching of HTML files so updates are immediately visible
@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith(".html"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# ─────────────────────────────────────────────
# Serve frontend static files
# ─────────────────────────────────────────────
FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
