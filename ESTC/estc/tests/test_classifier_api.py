from fastapi.testclient import TestClient
from estc.services.classifier_api.app.main import app

client = TestClient(app)

def test_healthz():
    response = client.get("/healthz")
    assert response.status_code == 200

def test_classify_bug():
    response = client.post("/classify", json={"text": "getting a 500 error"})
    assert response.json()["intent"] == "bug"

def test_classify_lockout():
    response = client.post("/classify", json={"text": "cannot login"})
    assert response.json()["intent"] == "lockout"