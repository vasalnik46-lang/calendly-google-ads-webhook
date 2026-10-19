import os
import logging
from fastapi import FastAPI, Request, HTTPException
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Загрузка переменных окружения для Google Ads
DEVELOPER_TOKEN = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN")
CLIENT_ID = os.getenv("GOOGLE_ADS_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_ADS_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("GOOGLE_ADS_REFRESH_TOKEN")
CUSTOMER_ID = os.getenv("GOOGLE_ADS_CUSTOMER_ID")
CONVERSION_ACTION_ID = os.getenv("GOOGLE_ADS_CONVERSION_ACTION_ID")


def get_google_ads_client():
    """Создает клиент Google Ads API из переменных окружения."""
    credentials = {
        "developer_token": DEVELOPER_TOKEN,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
        "customer_id": CUSTOMER_ID,
        "use_proto_plus": True,
    }
    return GoogleAdsClient.load_from_dict(credentials)


def send_conversion_to_google_ads(gclid: str):
    """Отправляет офлайн-конверсию в Google Ads по gclid."""
    try:
        client = get_google_ads_client()
        conversion_upload_service = client.get_service("ConversionUploadService")
        
        click_conversion = client.get_type("ClickConversion")
        click_conversion.conversion_action = (
            f"customers/{CUSTOMER_ID}/conversionActions/{CONVERSION_ACTION_ID}"
        )
        click_conversion.gclid = gclid
        
        # Устанавливаем время конверсии (текущее в UTC в формате YYYY-MM-DD HH:MM:SS+00:00)
        from datetime import datetime, timezone
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00")
        click_conversion.conversion_date_time = now_str
        
        request = client.get_type("UploadClickConversionsRequest")
        request.customer_id = CUSTOMER_ID
        request.conversions = [click_conversion]
        request.partial_failure = True
        
        response = conversion_upload_service.upload_click_conversions(request=request)
        
        if response.partial_failure_error:
            logger.error(f"❌ Ошибка partial failure при отправке: {response.partial_failure_error.message}")
        else:
            logger.info(f"✅ Конверсия для gclid {gclid} успешно отправлена в Google Ads!")
            
    except GoogleAdsException as ex:
        logger.error(f"❌ Ошибка Google Ads API: {ex}")
    except Exception as e:
        logger.error(f"❌ Системная ошибка при отправке конверсии: {e}")


@app.get("/")
async def root():
    return {"status": "ok", "message": "Calendly to Google Ads Webhook Service is running"}


@app.post("/webhook")
async def receive_webhook(request: Request):
    data = await request.json()
    logger.info(f"Received webhook event: {data}")

    # Проверяем тип события
    event_type = data.get("event")
    if event_type != "invitee.created":
        return {"status": "ignored", "reason": f"Event {event_type} is not invitee.created"}

    payload = data.get("payload", {})
    tracking = payload.get("tracking", {})
    
    # Извлекаем gclid из tracking (utm_content или напрямую)
    gclid = tracking.get("utm_content") or tracking.get("gclid")

    # Вспомогательный поиск в вопросах и ответах, если tracking пуст
    if not gclid:
        questions = payload.get("questions_and_answers", [])
        for qa in questions:
            answer = qa.get("answer", "")
            if answer and len(answer) > 5:
                gclid = answer
                break

    if gclid:
        logger.info(f"🎯 Найден gclid: {gclid}. Запускаем отправку в Google Ads...")
        send_conversion_to_google_ads(gclid)
    else:
        logger.info("ℹ️ gclid не найден в tracking. Пропускаем отправку.")

    return {"status": "success"}
