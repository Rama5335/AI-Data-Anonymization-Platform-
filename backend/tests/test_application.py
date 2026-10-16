from app.main import app


def test_application_metadata() -> None:
    assert app.title == "AI Data Anonymization Platform"
