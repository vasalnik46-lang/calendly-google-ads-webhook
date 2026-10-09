from fastapi import FastAPI, Request
import os
import uvicorn
from datetime import datetime, timezone

try:
    from google.ads.googleads.client import GoogleAdsClient
    from google.ads.googleads.errors import GoogleAdsException
except ImportError:
    GoogleAdsClient = None

app = FastAPI()

CUSTOMER_ID = os.getenv("GOOGLE_ADS_CUSTOMER_ID")
CONVERSION_ACTION_ID = os.getenv("CONVERSION_ACTION_ID")

def get_google_ads_client():
    """Инициализация клиента Google Ads из переменных окружения."""
    credentials = {
        "developer_token": os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN"),
        "client_id": os.getenv("GOOGLE_ADS_CLIENT_ID"),
        "client_secret": os.getenv("GOOGLE_ADS_CLIENT_SECRET"),
        "refresh_token": os.getenv("GOOGLE_ADS_REFRESH_TOKEN"),
        "use_proto_plus": True,
    }
    customer_id = CUSTOMER_ID.replace("-", "") if CUSTOMER_ID else None
    return GoogleAdsClient.load_from_dict(credentials), customer_id

def send_conversion_to_google_ads(gclid: str):
    """Отправка офлайн-конверсии в Google Ads API."""
    if not GoogleAdsClient:
        print("❌ Ошибка: Библиотека google-ads не установлена.")
        return

    try:
        client, customer_id = get_google_ads_client()
        conversion_upload_service = client.get_service("ConversionUploadService")
        
        click_conversion = client.get_type("ClickConversion")
        conversion_action_service = client.get_service("ConversionActionService")
        
        click_conversion.conversion_action = conversion_action_service.conversion_action_path(
            customer_id, CONVERSION_ACTION_ID
        )
        click_conversion.gclid = gclid
        click_conversion.conversion_date_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00")

        request = client.get_type("UploadClickConversionsRequest")
        request.customer_id = customer_id
        request.conversions.append(click_conversion)
        request.partial_failure = True

        response = conversion_upload_service.upload_click_conversions(request=request)
        
        if response.partial_failure_error:
            print(f"⚠️ Google Ads API вернул предупреждение/ошибку: {response.partial_failure_error.message}")
        else:
            print(f"🚀 Конверсия с gclid={gclid} успешно отправлена в Google Ads!")

    except GoogleAdsException as ex:
        print(f"❌ Ошибка Google Ads API: {ex}")
    except Exception as e:
        print(f"❌ Системная ошибка при отправке конверсии: {e}")

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Calendly Google Ads Webhook Server is running"}

@app.post("/webhook")
async def handle_webhook(request: Request):
    data = await request.json()
    print("Received webhook event:", data)

    payload = data.get("payload", {})
    tracking = payload.get("tracking", {})
    
    # Извлекаем gclid из utm_content или прямо из gclid
    gclid = tracking.get("utm_content") or tracking.get("gclid")

    if gclid:
        print(f"🎯 Найден gclid: {gclid}. Запускаем отправку в Google Ads...")
        send_conversion_to_google_ads(gclid)
    else:
        print("ℹ️ gclid не найден в tracking. Пропускаем отправку.")

    return {"status": "success"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
