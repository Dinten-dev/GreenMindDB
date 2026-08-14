import asyncio
import os
from app.services.notification_service import notification_service
from app.config import settings

async def main():
    phone_number = input("Enter your phone number to test (e.g., +41791234567): ")
    if not settings.aspsms_userkey:
        print("Error: ASPSMS_USERKEY is not set in your .env file or environment.")
        return
        
    print(f"Sending test SMS to {phone_number} using ASPSMS...")
    success = await notification_service.send_electrode_disconnect_alert(
        phone_number=phone_number,
        sensor_mac="TEST:MAC:12:34",
        zone_name="Test-Zone"
    )
    if success:
        print("SMS sent successfully!")
    else:
        print("Failed to send SMS. Check your credentials and logs.")

if __name__ == "__main__":
    asyncio.run(main())
