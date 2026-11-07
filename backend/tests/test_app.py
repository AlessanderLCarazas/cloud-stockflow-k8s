import pytest
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import app

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_cuenta_endpoint(client):
    """Verifica que el API responda correctamente con el saldo"""
    response = client.get('/api/cuenta')
    assert response.status_code == 200
    assert b"saldo" in response.data