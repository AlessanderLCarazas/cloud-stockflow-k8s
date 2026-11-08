
import os
import sys
import sqlite3
import pytest

sys.path.append(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

import app as app_module

app = app_module.app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """
    Crea una base SQLite independiente para cada prueba.
    No modifica la base local ni PostgreSQL.
    """

    db_path = tmp_path / "test_banco.db"

    def test_connection():
        conn = sqlite3.connect(
            str(db_path),
            timeout=10
        )
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(app_module, "MODO_K8S", False)
    monkeypatch.setattr(
        app_module,
        "get_db_connection",
        test_connection
    )

    conn = test_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE cuenta (
            id INTEGER PRIMARY KEY,
            titular TEXT,
            saldo REAL
        )
    """)

    cur.execute("""
        CREATE TABLE transacciones (
            id INTEGER PRIMARY KEY,
            tipo TEXT,
            monto REAL,
            cuenta_destino TEXT,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        INSERT INTO cuenta (id, titular, saldo)
        VALUES (1, 'Usuario de prueba', 1000.00)
    """)

    conn.commit()
    cur.close()
    conn.close()

    app.config["TESTING"] = True

    with app.test_client() as test_client:
        yield test_client


def obtener_saldo():
    """Consulta el saldo de la base temporal de pruebas."""

    conn = app_module.get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT saldo FROM cuenta WHERE id = 1")

    saldo = cur.fetchone()[0]

    cur.close()
    conn.close()

    return float(saldo)


def test_cuenta_endpoint(client):
    response = client.get("/api/cuenta")

    assert response.status_code == 200
    assert response.json["saldo"] == 1000.00
    assert response.json["motor_db"] == "SQLite Local"


def test_transferencia_exitosa(client):
    response = client.post(
        "/api/transfer",
        json={
            "monto": 100,
            "destino": "Cuenta 123"
        }
    )

    assert response.status_code == 200
    assert response.json["nuevo_saldo"] == 900.00
    assert obtener_saldo() == 900.00


def test_transferencia_sin_fondos(client):
    response = client.post(
        "/api/transfer",
        json={
            "monto": 2000,
            "destino": "Cuenta 123"
        }
    )

    assert response.status_code == 400
    assert obtener_saldo() == 1000.00


@pytest.mark.parametrize(
    "monto",
    [0, -10, "abc", float("inf"), float("nan")]
)
def test_transferencia_monto_invalido(client, monto):
    response = client.post(
        "/api/transfer",
        json={
            "monto": monto,
            "destino": "Cuenta 123"
        }
    )

    assert response.status_code == 400
    assert obtener_saldo() == 1000.00


def test_transferencia_destino_invalido(client):
    response = client.post(
        "/api/transfer",
        json={
            "monto": 100,
            "destino": ""
        }
    )

    assert response.status_code == 400
    assert obtener_saldo() == 1000.00


def test_transferencia_registra_movimiento(client):
    response = client.post(
        "/api/transfer",
        json={
            "monto": 50,
            "destino": "Cuenta 456"
        }
    )

    assert response.status_code == 200

    cuenta = client.get("/api/cuenta")

    assert len(cuenta.json["movimientos"]) == 1

    movimiento = cuenta.json["movimientos"][0]

    assert movimiento["destino"] == "Cuenta 456"
    assert movimiento["monto"] == 50.0


def test_transferencia_no_deja_saldo_negativo(client):
    response = client.post(
        "/api/transfer",
        json={
            "monto": 1500,
            "destino": "Cuenta 789"
        }
    )

    assert response.status_code == 400
    assert obtener_saldo() >= 0
