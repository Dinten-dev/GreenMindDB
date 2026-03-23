from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_contact_form_submission(mocker):
    """Test successful contact form submission uses mocked email sender."""
    mock_send = mocker.patch("app.routers.contact.send_notification_email")

    payload = {
        "name": "Test User",
        "email": "test@example.com",
        "company": "Test Company",
        "message": "This is a test message.",
        "website": "",  # Honeypot must be empty
    }

    response = client.post("/api/v1/contact", json=payload)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    # Ensure our email service was triggered
    mock_send.assert_called_once()
    args, _ = mock_send.call_args
    assert args[0] == "New GreenMind Website Contact Request"
    assert "Test User" in args[1]
    assert "test@example.com" in args[1]
    assert "Test Company" in args[1]
    assert "This is a test message." in args[1]


def test_contact_form_honeypot(mocker):
    """Test that honeypot field prevents actual email sending."""
    mock_send = mocker.patch("app.routers.contact.send_notification_email")

    payload = {
        "name": "Spam Bot",
        "email": "spam@example.com",
        "message": "Buy cheap stuff",
        "website": "http://spamsite.com",  # Honeypot filled
    }

    response = client.post("/api/v1/contact", json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    # Email should NOT be sent
    mock_send.assert_not_called()


def test_early_access_form_submission(mocker):
    """Test successful early access form submission uses mocked email sender."""
    mock_send = mocker.patch("app.routers.contact.send_notification_email")

    payload = {
        "name": "Early User",
        "company": "Early Farm",
        "email": "early@example.com",
        "country": "Schweiz",
        "message": "I want in.",
        "website": "",
    }

    response = client.post("/api/v1/early-access", json=payload)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    mock_send.assert_called_once()
    args, _ = mock_send.call_args
    assert args[0] == "New GreenMind Early Access Request"
    assert "Early User" in args[1]
    assert "Schweiz" in args[1]
