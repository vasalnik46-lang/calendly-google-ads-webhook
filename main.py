
import os
import logging
import re
from datetime import datetime, timezone

import requests
from fastapi import FastAPI, Request, HTTPException

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

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

TOKEN_URL = "https://oauth2.googleapis.com/token"
INGEST_URL = "https://datamanager.googleapis.com/v1/events:ingest"

GCLID_PATTERN = re.compile(r"[A-Za-z0-9_-]{20,}")


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
            detail=(
                "Missing environment variables: "
                + ", ".join(missing)
            ),
        )

    if not CUSTOMER_ID.isdigit():
        raise HTTPException(
            status_code=500,
            detail="Google Ads customer ID must contain digits only",
        )


def get_access_token():
    check_config()

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

    if not response.ok:
        logger.error(
            "OAuth token refresh failed: HTTP %s",
            response.status_code,
        )
        raise HTTPException(
            status_code=502,
            detail="Could not refresh Google OAuth token",
        )

    token = response.json().get("access_token")

    if not token:
        raise HTTPException(
            status_code=502,
            detail="Google OAuth response did not contain an access token",
        )

    return token


def parse_event_timestamp(value):
    if not value:
        return (
            datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
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

    response = requests.post(
        INGEST_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=30,
    )

    if not response.ok:
        # Не записываем GCLID, токены или тело запроса в логи.
        logger.error(
            "Data Manager API returned HTTP %s",
            response.status_code,
        )
        raise HTTPException(
            status_code=502,
            detail="Google Data Manager API rejected the event",
        )

    result = response.json()

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
        "message": "Calendly to Google Ads webhook is running",
        "validateOnly": VALIDATE_ONLY,
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
        logger.warning("Calendly diagnostic: payload is not an object")
        return {
            "status": "ignored",
            "reason": "Invalid payload structure",
        }

    tracking = payload.get("tracking") or {}

    if not isinstance(tracking, dict):
        logger.warning("Calendly diagnostic: tracking is not an object")
        tracking = {}

    # Логируем только названия полей и наличие значений.
    # Значения GCLID и другие персональные данные не выводятся.
    gclid_value = tracking.get("gclid")
    utm_content_value = tracking.get("utm_content")

    logger.info(
        "Calendly diagnostic: payload_keys=%s; tracking_keys=%s; "
        "gclid_present=%s; utm_content_present=%s",
        sorted(str(key) for key in payload.keys()),
        sorted(str(key) for key in tracking.keys()),
        bool(
            isinstance(gclid_value, str)
            and gclid_value.strip()
        ),
        bool(
            isinstance(utm_content_value, str)
            and utm_content_value.strip()
        ),
    )

    # Принимаем идентификатор только из выделенных tracking-полей.
    candidates = [gclid_value, utm_content_value]
    gclid = next(
        (
            value.strip()
            for value in candidates
            if isinstance(value, str)
            and GCLID_PATTERN.fullmatch(value.strip())
        ),
        "",
    )

    if not gclid:
        logger.info(
            "Calendly event ignored: no valid-looking GCLID "
            "in tracking.gclid or tracking.utm_content"
        )
        return {
            "status": "ignored",
            "reason": "No valid-looking GCLID in tracking fields",
        }

    event_timestamp = parse_event_timestamp(
        payload.get("created_at")
    )

    uri = payload.get("uri") or ""
    transaction_id = (
        uri.rstrip("/").split("/")[-1]
        if isinstance(uri, str) and uri
        else ""
    )

    return send_conversion_to_data_manager(
        gclid=gclid,
        event_timestamp=event_timestamp,
        transaction_id=transaction_id,
    )
