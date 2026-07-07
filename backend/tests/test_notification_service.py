import pytest
from unittest.mock import AsyncMock, patch
from app.services.notification_service import ASPSMSAdapter, NotificationService

@pytest.mark.asyncio
async def test_aspsms_adapter_send_success():
    adapter = ASPSMSAdapter()
    adapter.userkey = "test_key"
    adapter.password = "test_pass"
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_response = AsyncMock()
        mock_response.json.return_value = {"StatusCode": "1", "StatusInfo": "OK"}
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response
        
        result = await adapter.send_sms("+41791234567", "Test message")
        
        assert result is True
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        assert kwargs["json"]["Recipients"] == ["+41791234567"]
        assert kwargs["json"]["MessageText"] == "Test message"

@pytest.mark.asyncio
async def test_aspsms_adapter_send_failure():
    adapter = ASPSMSAdapter()
    adapter.userkey = "test_key"
    adapter.password = "test_pass"
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_response = AsyncMock()
        mock_response.json.return_value = {"StatusCode": "0", "StatusInfo": "Auth failed"}
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response
        
        result = await adapter.send_sms("+41791234567", "Test message")
        
        assert result is False

@pytest.mark.asyncio
async def test_notification_service_skips_empty_phone():
    service = NotificationService()
    service.adapter = AsyncMock()
    
    result = await service.send_electrode_disconnect_alert("", "00:11:22:33:44:55", "Zone 1")
    assert result is False
    service.adapter.send_sms.assert_not_called()

@pytest.mark.asyncio
async def test_notification_service_calls_adapter():
    service = NotificationService()
    service.adapter = AsyncMock()
    service.adapter.send_sms.return_value = True
    
    result = await service.send_electrode_disconnect_alert("+41791234567", "00:11:22:33:44:55", "Zone 1")
    assert result is True
    service.adapter.send_sms.assert_called_once()
    _, kwargs = service.adapter.send_sms.call_args
    assert "+41791234567" in kwargs or "+41791234567" in service.adapter.send_sms.call_args[0]
