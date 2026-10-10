from fastapi.testclient import TestClient
from app.main import app


def test_confirmation_page_has_feedback_without_echoing_tokens():
    response = TestClient(app).get('/auth/confirmed?error=expired&access_token=private-test-token')
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/html')
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert 'Return to FindBack' in response.text
    assert 'sign in' in response.text
    assert 'private-test-token' not in response.text
    assert 'history.replaceState' in response.text
    assert 'expired' in response.text
