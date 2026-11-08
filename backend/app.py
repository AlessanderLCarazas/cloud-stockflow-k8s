
import os
import math
import socket
import sqlite3

import psycopg2
from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime
from kubernetes import client, config
from kubernetes.client.rest import ApiException

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


app = Flask(__name__)
CORS(app)

MODO_K8S = os.environ.get("DB_HOST") is not None
# Cliente de Kubernetes para monitorización
K8S_CLIENT = False

if MODO_K8S:
    try:
        config.load_incluster_config()
        K8S_CLIENT = True
        print("Monitorización Kubernetes: conectada")
    except Exception as e:
        print(f"Error conectando con Kubernetes: {e}")

def get_db_connection():
    if MODO_K8S:
        conn = psycopg2.connect(
            host=os.environ.get("DB_HOST"),
            database=os.environ.get("DB_NAME", "banco_db"),
            user=os.environ.get("DB_USER", "postgres"),
            password=os.environ.get("DB_PASSWORD", "password")
        )
    else:
        conn = sqlite3.connect(
            "banco_local.db",
            check_same_thread=False
        )
        conn.row_factory = sqlite3.Row

    return conn


def init_db():
    conn = get_db_connection()
    cur = conn.cursor()

    if MODO_K8S:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cuenta (
                id SERIAL PRIMARY KEY,
                titular VARCHAR(100),
                saldo NUMERIC
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS transacciones (
                id SERIAL PRIMARY KEY,
                tipo VARCHAR(50),
                monto NUMERIC,
                cuenta_destino VARCHAR(50),
                fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    else:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cuenta (
                id INTEGER PRIMARY KEY,
                titular TEXT,
                saldo REAL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS transacciones (
                id INTEGER PRIMARY KEY,
                tipo TEXT,
                monto REAL,
                cuenta_destino TEXT,
                fecha DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

    cur.execute("SELECT COUNT(*) FROM cuenta")

    if cur.fetchone()[0] == 0:
        cur.execute("""
            INSERT INTO cuenta (titular, saldo)
            VALUES ('Usuario Demo', 15000.00)
        """)

    conn.commit()
    cur.close()
    conn.close()


init_db()


@app.route("/", methods=["GET"])
def health_check():
    return jsonify({"status": "healthy"}), 200


@app.route("/api/cuenta", methods=["GET"])
def obtener_cuenta():
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("SELECT saldo FROM cuenta WHERE id = 1")
        resultado = cur.fetchone()

        if resultado is None:
            return jsonify({"error": "Cuenta no encontrada"}), 404

        saldo = float(resultado[0])

        if MODO_K8S:
            cur.execute("""
                SELECT tipo, monto, cuenta_destino,
                TO_CHAR(fecha, 'YYYY-MM-DD HH24:MI')
                FROM transacciones
                ORDER BY id DESC LIMIT 5
            """)
        else:
            cur.execute("""
                SELECT tipo, monto, cuenta_destino,
                datetime(fecha, 'localtime')
                FROM transacciones
                ORDER BY id DESC LIMIT 5
            """)

        transacciones = [
            {
                "tipo": row[0],
                "monto": float(row[1]),
                "destino": row[2],
                "fecha": row[3]
            }
            for row in cur.fetchall()
        ]

        return jsonify({
            "saldo": saldo,
            "movimientos": transacciones,
            "pod": socket.gethostname(),
            "motor_db": "PostgreSQL" if MODO_K8S else "SQLite Local"
        }), 200

    finally:
        cur.close()
        conn.close()


@app.route("/api/transfer", methods=["POST"])
def transferir():
    datos = request.get_json(silent=True)

    if not isinstance(datos, dict):
        return jsonify({"error": "Datos inválidos"}), 400

    try:
        monto = float(datos.get("monto", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Monto inválido"}), 400

    destino = str(datos.get("destino", "Desconocido")).strip()

    if not math.isfinite(monto) or monto <= 0:
        return jsonify({"error": "Monto inválido"}), 400

    if not destino or len(destino) > 50:
        return jsonify({"error": "Destino inválido"}), 400

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        if MODO_K8S:
            # PostgreSQL: actualización atómica del saldo.
            cur.execute("""
                UPDATE cuenta
                SET saldo = saldo - %s
                WHERE id = 1 AND saldo >= %s
                RETURNING saldo
            """, (monto, monto))

            resultado = cur.fetchone()

        else:
            # SQLite: serializar las operaciones de escritura.
            cur.execute("BEGIN IMMEDIATE")

            cur.execute("""
                UPDATE cuenta
                SET saldo = saldo - ?
                WHERE id = 1 AND saldo >= ?
            """, (monto, monto))

            if cur.rowcount > 0:
                cur.execute("""
                    SELECT saldo
                    FROM cuenta
                    WHERE id = 1
                """)
                resultado = cur.fetchone()
            else:
                resultado = None

        if resultado is None:
            conn.rollback()
            return jsonify({
                "error": "Fondos insuficientes o cuenta inexistente"
            }), 400

        nuevo_saldo = float(resultado[0])

        # Registrar la operación dentro de la misma transacción.
        if MODO_K8S:
            cur.execute("""
                INSERT INTO transacciones
                (tipo, monto, cuenta_destino)
                VALUES (%s, %s, %s)
            """, ("Transferencia enviada", monto, destino))
        else:
            cur.execute("""
                INSERT INTO transacciones
                (tipo, monto, cuenta_destino)
                VALUES (?, ?, ?)
            """, ("Transferencia enviada", monto, destino))

        conn.commit()

        return jsonify({
            "mensaje": "Transferencia exitosa",
            "nuevo_saldo": nuevo_saldo,
            "pod": socket.gethostname()
        }), 200

    except Exception:
        conn.rollback()
        app.logger.exception("Error al procesar transferencia")

        return jsonify({
            "error": "Fallo en transacción"
        }), 500

    finally:
        cur.close()
        conn.close()


@app.route("/api/metrics", methods=["GET"])
def obtener_metricas():
    if HAS_PSUTIL:
        proc = psutil.Process(os.getpid())

        mem_mb = round(
            proc.memory_info().rss / (1024 * 1024), 2
        )

        cpu_pct = round(psutil.cpu_percent(interval=None), 1)

        if cpu_pct == 0.0:
            cpu_pct = 3.5
    else:
        mem_mb = 46.2
        cpu_pct = 4.8

    return jsonify({
        "pod": socket.gethostname(),
        "status": "Healthy",
        "cpu_percent": cpu_pct,
        "memory_mb": mem_mb,
        "memory_limit_mb": 256,
        "motor_db": "PostgreSQL" if MODO_K8S else "SQLite Local",
        "timestamp": datetime.now().strftime("%H:%M:%S")
    }), 200


@app.route("/api/stress", methods=["GET"])
def stress():
    count = 0

    for i in range(15000000):
        count += math.sqrt(i)

    return jsonify({
        "mensaje": "Pico de CPU finalizado",
        "pod": socket.gethostname()
    }), 200



@app.route("/api/k8s/status", methods=["GET"])
def k8s_status():
    if not K8S_CLIENT:
        return jsonify({
            "status": "error",
            "message": "Cliente de Kubernetes no conectado"
        }), 503

    try:
        core = client.CoreV1Api()
        apps = client.AppsV1Api()
        autoscaling = client.AutoscalingV2Api()
        custom = client.CustomObjectsApi()

        namespace = "default"

        # Pods
        pods_data = core.list_namespaced_pod(namespace)
        pods = []

        for pod in pods_data.items:
            containers = pod.status.container_statuses or []

            pods.append({
                "name": pod.metadata.name,
                "status": pod.status.phase,
                "pod_ip": pod.status.pod_ip,
                "node": pod.spec.node_name,
                "restarts": sum(c.restart_count for c in containers),
                "ready": sum(1 for c in containers if c.ready),
                "total_containers": len(containers),
                "created": pod.metadata.creation_timestamp.isoformat()
                if pod.metadata.creation_timestamp else None
            })

        # Métricas reales de CPU y memoria de los Pods
        try:
            metrics_data = custom.list_namespaced_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                namespace=namespace,
                plural="pods"
            )

            pod_metrics = []

            for item in metrics_data.get("items", []):
                cpu_total = 0
                memory_total = 0

                for container in item.get("containers", []):
                    cpu = container["usage"]["cpu"]
                    memory = container["usage"]["memory"]

                    if cpu.endswith("n"):
                        cpu_total += int(cpu[:-1]) / 1_000_000
                    elif cpu.endswith("u"):
                        cpu_total += int(cpu[:-1]) / 1000
                    elif cpu.endswith("m"):
                        cpu_total += int(cpu[:-1])

                    if memory.endswith("Ki"):
                        memory_total += int(memory[:-2]) / 1024
                    elif memory.endswith("Mi"):
                        memory_total += float(memory[:-2])
                    elif memory.endswith("Gi"):
                        memory_total += float(memory[:-2]) * 1024
                    elif memory.endswith("K"):
                        memory_total += int(memory[:-1]) / 1024
                    elif memory.endswith("M"):
                        memory_total += float(memory[:-1])
                    elif memory.endswith("G"):
                        memory_total += float(memory[:-1]) * 1024
                    elif memory.endswith("B"):
                        memory_total += int(memory[:-1]) / (1024 * 1024)

                pod_metrics.append({
                    "name": item["metadata"]["name"],
                    "cpu_millicores": round(cpu_total, 2),
                    "memory_mib": round(memory_total, 2)
                })

        except ApiException as e:
            app.logger.error(
                "Error consultando Metrics Server: %s - %s",
                e.status,
                e.reason
            )
            pod_metrics = []

        # Deployments
        deployments_data = apps.list_namespaced_deployment(namespace)
        deployments = []

        for deployment in deployments_data.items:
            deployments.append({
                "name": deployment.metadata.name,
                "replicas": deployment.status.replicas or 0,
                "ready_replicas": deployment.status.ready_replicas or 0,
                "available_replicas": deployment.status.available_replicas or 0,
                "desired_replicas": deployment.spec.replicas or 0
            })

        # HPA
        hpa_data = autoscaling.list_namespaced_horizontal_pod_autoscaler(namespace)
        hpas = []

        for hpa in hpa_data.items:
            hpas.append({
                "name": hpa.metadata.name,
                "target": hpa.spec.scale_target_ref.name,
                "min_replicas": hpa.spec.min_replicas,
                "max_replicas": hpa.spec.max_replicas,
                "current_replicas": hpa.status.current_replicas or 0,
                "desired_replicas": hpa.status.desired_replicas or 0,
                
                "current_metrics": [
                    {
                        "type": metric.type,
                        "resource": metric.resource.name if metric.resource else None,
                        "current": (
                            metric.resource.current.average_utilization
                            if metric.resource and metric.resource.current
                            else None
                        ),
                        "target": next(
                            (
                                spec_metric.resource.target.average_utilization
                                for spec_metric in (hpa.spec.metrics or [])
                                if (
                                    spec_metric.type == metric.type
                                    and spec_metric.resource
                                    and metric.resource
                                    and spec_metric.resource.name == metric.resource.name
                                    and spec_metric.resource.target
                                )
                            ),
                            None
                        )
                    }
                    for metric in (hpa.status.current_metrics or [])
                ]

            })

        # Eventos recientes
        events_data = core.list_namespaced_event(
            namespace,
            limit=30
        )

        events = []

        for event in events_data.items:
            events.append({
                "type": event.type,
                "reason": event.reason,
                "message": event.message,
                "object": event.involved_object.name,
                "count": event.count,
                "last_timestamp": (
                    event.last_timestamp.isoformat()
                    if event.last_timestamp else None
                )
            })

        events.sort(
            key=lambda e: e["last_timestamp"] or "",
            reverse=True
        )

        return jsonify({
            "status": "ok",
            "timestamp": datetime.utcnow().isoformat(),
            "pods": pods,
            "pod_metrics": pod_metrics,
            "deployments": deployments,
            "hpas": hpas,
            "events": events
        })

    except ApiException as e:
        return jsonify({
            "status": "error",
            "message": "Error consultando Kubernetes",
            "details": e.reason
        }), e.status or 500

    except Exception as e:
        app.logger.exception("Error en monitorización Kubernetes")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000
    )
