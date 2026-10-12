@app.post("/webhook")
async def handle_webhook(request: Request):
    data = await request.json()
    print("Received webhook event:", data)

    payload = data.get("payload", {})
    tracking = payload.get("tracking", {})
    
    # 1. Проверяем стандартный tracking
    gclid = tracking.get("utm_content") or tracking.get("gclid")

    # 2. Если не нашли, ищем в questions_and_answers (если передаем через параметры формы)
    if not gclid:
        questions = payload.get("questions_and_answers", [])
        for qa in questions:
            # Если текст вопроса или ответа содержит gclid
            answer = qa.get("answer", "")
            if answer and len(answer) > 10 and ("gclid" in answer.lower() or not " " in answer):
                gclid = answer
                break

    if gclid:
        print(f"🎯 Найден gclid: {gclid}. Запускаем отправку в Google Ads...")
        send_conversion_to_google_ads(gclid)
    else:
        print("ℹ️ gclid не найден. Пропускаем отправку.")

    return {"status": "success"}
