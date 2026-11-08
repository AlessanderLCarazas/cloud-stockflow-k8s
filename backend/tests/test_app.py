
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


def test_k8s_status_includes_worker_node_metrics(client, monkeypatch):
    """Comprueba los nodos, providerID y métricas de infraestructura."""

    from types import SimpleNamespace as NS

    worker = NS(
        metadata=NS(
            name="worker-test",
            labels={
                "cloudstockflow/role": "worker",
                "node.kubernetes.io/instance-type": "t3.medium",
                "topology.kubernetes.io/zone": "us-east-1d",
            },
        ),
        spec=NS(
            provider_id="aws:///us-east-1d/i-0123456789abcdef0"
        ),
        status=NS(
            conditions=[
                NS(type="Ready", status="True")
            ],
            addresses=[
                NS(type="InternalIP", address="172.31.29.35")
            ],
            capacity={
                "cpu": "2",
                "memory": "3939996Ki",
                "pods": "110",
            },
            allocatable={
                "cpu": "2",
                "memory": "3939996Ki",
                "pods": "110",
            },
        ),
    )

    fake_core = NS(
        list_namespaced_pod=lambda *args, **kwargs: NS(items=[]),
        list_node=lambda *args, **kwargs: NS(items=[worker]),
        list_namespaced_event=lambda *args, **kwargs: NS(items=[]),
    )

    fake_apps = NS(
        list_namespaced_deployment=lambda *args, **kwargs: NS(items=[])
    )

    fake_autoscaling = NS(
        list_namespaced_horizontal_pod_autoscaler=lambda *args, **kwargs:
            NS(items=[])
    )

    def fake_cluster_metrics(group, version, plural, **kwargs):
        if plural == "pods":
            return {"items": []}

        if plural == "nodes":
            return {
                "items": [{
                    "metadata": {"name": "worker-test"},
                    "usage": {
                        "cpu": "250m",
                        "memory": "512Mi",
                    },
                }]
            }

        return {"items": []}

    fake_custom = NS(
        list_namespaced_custom_object=lambda *args, **kwargs: {"items": []},
        list_cluster_custom_object=fake_cluster_metrics,
    )

    monkeypatch.setattr(
        app_module.client, "CoreV1Api", lambda: fake_core
    )
    monkeypatch.setattr(
        app_module.client, "AppsV1Api", lambda: fake_apps
    )
    monkeypatch.setattr(
        app_module.client, "AutoscalingV2Api",
        lambda: fake_autoscaling
    )
    monkeypatch.setattr(
        app_module.client, "CustomObjectsApi", lambda: fake_custom
    )
    monkeypatch.setattr(app_module, "K8S_CLIENT", True)

    response = client.get("/api/k8s/status")

    assert response.status_code == 200

    data = response.get_json()
    assert data["status"] == "ok"
    assert len(data["nodes"]) == 1

    node = data["nodes"][0]

    assert node["name"] == "worker-test"
    assert node["role"] == "worker"
    assert node["status"] == "Ready"
    assert node["instance_id"] == "i-0123456789abcdef0"
    assert node["instance_type"] == "t3.medium"
    assert node["availability_zone"] == "us-east-1d"
    assert node["private_ip"] == "172.31.29.35"

    assert node["usage"]["cpu_millicores"] == 250
    assert node["usage"]["cpu_percent"] == 12.5
    assert node["usage"]["memory_mib"] == 512
    assert node["usage"]["memory_percent"] is not None

    assert node["capacity"]["cpu_cores"] == 2
    assert node["capacity"]["pods"] == 110
