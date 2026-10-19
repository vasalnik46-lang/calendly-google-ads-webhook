
import os
import logging
import re
from datetime import datetime, timezone

import requests
from fastapi import FastAPI, Request, HTTPException

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Google Ads configuration
CLIENT_ID = os.getenv("GOOGLE_ADS_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_ADS_CLIENT_SECRET")
REFRESH_TOKEN = (
    os.getenv("GOOGLE_REFRESH_TOKEN")
    or os.getenv("GOOGLE_ADS_REFRESH_TOKEN")
)
CUSTOMER_ID = (
    os.getenv("GOOGLE_ADS_CUSTOMER_ID") or ""
).replace("-", "")
CONVERSION_ACTION_ID = (
    os.getenv("CONVERSION_ACTION_ID")
    or os.getenv("GOOGLE_ADS_CONVERSION_ACTION_ID")
)

VALIDATE_ONLY = (
    os.getenv("DATA_MANAGER_VALIDATE_ONLY", "true").lower()
    == "true"
)

# Google Analytics 4 configuration
GA4_MEASUREMENT_ID = os.getenv("GA4_MEASUREMENT_ID")
GA4_API_SECRET = os.getenv("GA4_API_SECRET")

TOKEN_URL = "https://oauth2.googleapis.com/token"
INGEST_URL = "https://datamanager.googleapis.com/v1/events:ingest"
GA4_MP_URL = "https://www.google-analytics.com/mp/collect"

GCLID_PATTERN = re.compile(r"[A-Za-z0-9_-]{20,}")
GA4_CLIENT_ID_PATTERN = re.compile(r"\d+\.\d+")
GA4_SESSION_ID_PATTERN = re.compile(r"\d+")


def check_config():
    required = {
        "GOOGLE_ADS_CLIENT_ID": CLIENT_ID,
        "GOOGLE_ADS_CLIENT_SECRET": CLIENT_SECRET,
        "GOOGLE_REFRESH_TOKEN": REFRESH_TOKEN,
        "GOOGLE_ADS_CUSTOMER_ID": CUSTOMER_ID,
        "CONVERSION_ACTION_ID": CONVERSION_ACTION_ID,
    }

    missing = [
        name for name, value in required.items() if not value
    ]

    if missing:
        raise HTTPException(
            status_code=500,
            detail="Missing environment variables: "
            + ", ".join(missing),
        )

    if not CUSTOMER_ID.isdigit():
        raise HTTPException(
            status_code=500,
            detail="Google Ads customer ID must contain digits only",
        )


def get_access_token():
    check_config()

    try:
        response = requests.post(
            TOKEN_URL,
            data={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "refresh_token": REFRESH_TOKEN,
                "grant_type": "refresh_token",
            },
            timeout=20,
        )
    except requests.RequestException:
        logger.error("Google OAuth request failed")
        raise HTTPException(
            status_code=502,
            detail="Could not reach Google OAuth",
        )

    if not response.ok:
        logger.error(
            "OAuth token refresh failed: HTTP %s",
            response.status_code,
        )
        raise HTTPException(
            status_code=502,
            detail="Could not refresh Google OAuth token",
        )

    try:
        token = response.json().get("access_token")
    except ValueError:
        token = None

    if not token:
        raise HTTPException(
            status_code=502,
            detail="Google OAuth response did not contain an access token",
        )

    return token


def parse_event_timestamp(value):
    if not value or not isinstance(value, str):
        raise HTTPException(
            status_code=400,
            detail="Calendly event timestamp is missing or invalid",
        )

    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return (
            parsed.astimezone(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )

    except (ValueError, TypeError, AttributeError):
        raise HTTPException(
            status_code=400,
            detail="Invalid Calendly event timestamp",
        )


def timestamp_to_micros(value):
    parsed = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return str(int(parsed.timestamp() * 1_000_000))


def parse_ga4_tracking(tracking):
    """
    Supported tracking.utm_content formats:

    Legacy:
        GCLID

    Current:
        GCLID~GA4_CLIENT_ID~GA4_SESSION_ID
    """

    gclid = ""
    client_id = ""
    session_id = ""

    raw_gclid = tracking.get("gclid")

    if (
        isinstance(raw_gclid, str)
        and GCLID_PATTERN.fullmatch(raw_gclid.strip())
    ):
        gclid = raw_gclid.strip()

    raw_content = tracking.get("utm_content")
    content = (
        raw_content.strip()
        if isinstance(raw_content, str)
        else ""
    )

    if not gclid and GCLID_PATTERN.fullmatch(content):
        gclid = content

    parts = content.split("~")

    if len(parts) == 3:
        candidate_gclid, candidate_client_id, candidate_session_id = parts

        if (
            not gclid
            and GCLID_PATTERN.fullmatch(candidate_gclid)
        ):
            gclid = candidate_gclid

        if GA4_CLIENT_ID_PATTERN.fullmatch(candidate_client_id):
            client_id = candidate_client_id

        if GA4_SESSION_ID_PATTERN.fullmatch(candidate_session_id):
            session_id = candidate_session_id

    return gclid, client_id, session_id


def send_booking_to_ga4(
    client_id,
    session_id,
    transaction_id,
    event_timestamp,
):
    if not GA4_MEASUREMENT_ID or not GA4_API_SECRET:
        logger.error(
            "GA4 event not sent: configuration is missing"
        )
        return {"status": "not_configured"}

    if not GA4_CLIENT_ID_PATTERN.fullmatch(client_id):
        return {
            "status": "skipped",
            "reason": "Missing or invalid GA4 client_id",
        }

    if not GA4_SESSION_ID_PATTERN.fullmatch(session_id):
        return {
            "status": "skipped",
            "reason": "Missing or invalid GA4 session_id",
        }

    body = {
        "client_id": client_id,
        "timestamp_micros": timestamp_to_micros(event_timestamp),
        "events": [
            {
                "name": "booking_completed",
                "params": {
                    "session_id": int(session_id),
                    "engagement_time_msec": 1,
                    "booking_id": transaction_id,
                },
            }
        ],
    }

    try:
        response = requests.post(
            GA4_MP_URL,
            params={
                "measurement_id": GA4_MEASUREMENT_ID,
                "api_secret": GA4_API_SECRET,
            },
            json=body,
            timeout=20,
        )
    except requests.RequestException:
        logger.error("GA4 Measurement Protocol request failed")
        return {"status": "request_failed"}

    if not response.ok:
        logger.error(
            "GA4 Measurement Protocol returned HTTP %s",
            response.status_code,
        )
        return {
            "status": "rejected",
            "http_status": response.status_code,
        }

    logger.info(
        "GA4 booking event request accepted; "
        "event=booking_completed"
    )

    return {
        "status": "sent",
        "event": "booking_completed",
    }


def send_conversion_to_data_manager(
    gclid,
    event_timestamp,
    transaction_id,
):
    access_token = get_access_token()

    event = {
        "eventTimestamp": event_timestamp,
        "eventSource": "WEB",
        "adIdentifiers": {
            "gclid": gclid,
        },
    }

    if transaction_id:
        event["transactionId"] = transaction_id

    body = {
        "destinations": [
            {
                "operatingAccount": {
                    "accountType": "GOOGLE_ADS",
                    "accountId": CUSTOMER_ID,
                },
                "loginAccount": {
                    "accountType": "GOOGLE_ADS",
                    "accountId": CUSTOMER_ID,
                },
                "productDestinationId": str(
                    CONVERSION_ACTION_ID
                ),
            }
        ],
        "events": [event],
        "validateOnly": VALIDATE_ONLY,
    }

    try:
        response = requests.post(
            INGEST_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=30,
        )
    except requests.RequestException:
        logger.error("Data Manager request failed")
        raise HTTPException(
            status_code=502,
            detail="Could not reach Google Data Manager API",
        )

    if not response.ok:
        logger.error(
            "Data Manager API returned HTTP %s",
            response.status_code,
        )
        raise HTTPException(
            status_code=502,
            detail="Google Data Manager API rejected the event",
        )

    try:
        result = response.json()
    except ValueError:
        result = {}

    logger.info(
        "Data Manager request accepted; validateOnly=%s; requestId=%s",
        VALIDATE_ONLY,
        result.get("requestId", "not returned"),
    )

    return {
        "status": "validated" if VALIDATE_ONLY else "submitted",
        "validateOnly": VALIDATE_ONLY,
        "requestId": result.get("requestId"),
        "fieldWarnings": result.get("fieldWarnings", []),
    }


@app.get("/")
async def root():
    return {
        "status": "ok",
        "message": "Calendly to Google Ads and GA4 webhook is running",
        "validateOnly": VALIDATE_ONLY,
        "ga4Configured": bool(
            GA4_MEASUREMENT_ID and GA4_API_SECRET
        ),
    }


@app.post("/webhook")
async def receive_webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid JSON",
        )

    if not isinstance(data, dict):
        raise HTTPException(
            status_code=400,
            detail="Webhook body must be a JSON object",
        )

    event_type = data.get("event")

    if event_type != "invitee.created":
        logger.info(
            "Calendly event ignored; event_type=%s",
            event_type if isinstance(event_type, str) else "missing",
        )
        return {
            "status": "ignored",
            "reason": "Event is not invitee.created",
        }

    payload = data.get("payload") or {}

    if not isinstance(payload, dict):
        logger.warning("Calendly payload is not an object")
        return {
            "status": "ignored",
            "reason": "Invalid payload structure",
        }

    tracking = payload.get("tracking") or {}

    if not isinstance(tracking, dict):
        logger.warning("Calendly tracking is not an object")
        tracking = {}

    logger.info(
        "Calendly diagnostic: payload_keys=%s; tracking_keys=%s",
        sorted(str(key) for key in payload.keys()),
        sorted(str(key) for key in tracking.keys()),
    )

    gclid, client_id, session_id = parse_ga4_tracking(tracking)

    logger.info(
        "Tracking diagnostics: gclid_present=%s; "
        "ga4_client_id_present=%s; ga4_session_id_present=%s",
        bool(gclid),
        bool(client_id),
        bool(session_id),
    )

    event_timestamp = parse_event_timestamp(
        payload.get("created_at")
    )

    uri = payload.get("uri") or ""
    transaction_id = (
        uri.rstrip("/").split("/")[-1]
        if isinstance(uri, str) and uri
        else ""
    )

    results = {
        "status": "processed",
        "google_ads": {
            "status": "skipped",
            "reason": "No valid-looking GCLID",
        },
        "ga4": {
            "status": "skipped",
            "reason": "GA4 client_id/session_id not available",
        },
    }

    if client_id and session_id:
        results["ga4"] = send_booking_to_ga4(
            client_id=client_id,
            session_id=session_id,
            transaction_id=transaction_id,
            event_timestamp=event_timestamp,
        )

    if gclid:
        results["google_ads"] = send_conversion_to_data_manager(
            gclid=gclid,
            event_timestamp=event_timestamp,
            transaction_id=transaction_id,
        )

    if not gclid and not (client_id and session_id):
        results["status"] = "ignored"
        results["reason"] = (
            "No valid GCLID or GA4 client/session identifiers"
        )

    return results
