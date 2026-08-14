"""Notification service for sending alerts via ASPSMS and AWS SNS."""

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
import json

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

class SMSAdapter(ABC):
    @abstractmethod
    async def send_sms(self, phone_number: str, message: str) -> bool:
        pass


class ASPSMSAdapter(SMSAdapter):
    def __init__(self):
        self.base_url = "https://webapi.aspsms.com/api"
        self.userkey = settings.aspsms_userkey
        self.password = settings.aspsms_password
        self.sender_id = settings.aspsms_sender_id

    async def send_sms(self, phone_number: str, message: str) -> bool:
        if not self.userkey or not self.password:
            logger.warning("ASPSMS not configured, skipping SMS to %s", phone_number)
            return False

        url = "https://json.aspsms.com/SendTextSMS"
        payload = {
            "UserName": self.userkey,
            "Password": self.password,
            "Originator": self.sender_id,
            "Recipients": [phone_number],
            "MessageText": message
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                
                status_code = data.get("StatusCode")
                if status_code == "1":
                    logger.info(f"ASPSMS sent successfully to {phone_number}")
                    return True
                else:
                    logger.error(f"ASPSMS failed to {phone_number}: {data.get('StatusInfo')}")
                    return False
        except Exception as e:
            logger.error(f"ASPSMS exception to {phone_number}: {e}")
            return False


class SNSAdapter(SMSAdapter):
    def __init__(self):
        self.region = settings.s3_region  # reusing region
        # Placeholder for AWS SNS
        pass

    async def send_sms(self, phone_number: str, message: str) -> bool:
        logger.warning(f"AWS SNS Adapter called for {phone_number}, but not fully implemented.")
        return False


class NotificationService:
    def __init__(self):
        # We default to ASPSMS if it is configured
        if settings.aspsms_userkey:
            self.adapter: SMSAdapter = ASPSMSAdapter()
        else:
            self.adapter = SNSAdapter()

    async def send_electrode_disconnect_alert(self, phone_number: str, sensor_mac: str, zone_name: str | None = None):
        if not phone_number:
            logger.warning("No phone number provided for disconnect alert, skipping.")
            return False

        location = f"Zone {zone_name}" if zone_name else "einem Sensor"
        time_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
        
        message = (
            f"GreenMind Alert: Elektrode ab! "
            f"Sensor {sensor_mac} in {location} hat das Pflanzensignal verloren ({time_str}). "
            f"Bitte prüfen und ggf. neu ankleben."
        )
        
        return await self.adapter.send_sms(phone_number, message)


notification_service = NotificationService()
