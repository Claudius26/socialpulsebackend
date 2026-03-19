import os
from twilio.rest import Client

def send_admin_whatsapp(message: str) -> str:
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    wa_from = os.getenv("TWILIO_WHATSAPP_FROM")
    wa_to = os.getenv("ADMIN_WHATSAPP_TO")

    if not all([sid, token, wa_from, wa_to]):
        raise Exception("Missing Twilio WhatsApp env vars")

    client = Client(sid, token)
    msg = client.messages.create(body=message, from_=wa_from, to=wa_to)
    return msg.sid