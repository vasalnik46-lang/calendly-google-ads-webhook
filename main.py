from fastapi import FastAPI, Request
import os
import uvicorn

app = FastAPI()

GOOGLE_ADS_CUSTOMER_ID = os.getenv("GOOGLE_ADS_CUSTOMER_ID")
CONVERSION_ACTION_ID = os.getenv("CONVERSION_ACTION_ID")

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Calendly Google Ads Webhook Server is running"}

@app.post("/webhook")
async def handle_webhook(request: Request):
    data = await request.json()
    print("Received webhook event:", data)
    return {"status": "success"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
