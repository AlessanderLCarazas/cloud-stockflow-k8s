
import json
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

        # Estado del provisionador EC2: lectura de solo lectura desde ConfigMap.
        provisioner_status = {
            "status": "unavailable",
            "message": "El publicador todavía no ha proporcionado datos."
        }

        try:
            status_configmap = core.read_namespaced_config_map(
                name="cloud-stockflow-provisioner-status",
                namespace=namespace,
                _request_timeout=5
            )

            raw_status = (status_configmap.data or {}).get("status")

            if raw_status:
                provisioner_status = json.loads(raw_status)

                # Detectar datos antiguos si el publicador deja de funcionar.
                updated_at = datetime.fromisoformat(
                    provisioner_status["updated_at"]
                )
                current_time = (
                    datetime.now(updated_at.tzinfo)
                    if updated_at.tzinfo
                    else datetime.utcnow()
                )
                age_seconds = max(
                    0, int((current_time - updated_at).total_seconds())
                )

                provisioner_status["age_seconds"] = age_seconds
                provisioner_status["status"] = (
                    "ok" if age_seconds <= 90 else "stale"
                )
            else:
                provisioner_status["message"] = (
                    "El ConfigMap existe, pero no contiene datos."
                )

        except ApiException as e:
            provisioner_status["status"] = (
                "forbidden" if e.status == 403 else "unavailable"
            )
            provisioner_status["message"] = (
                "No se pudo consultar el estado del provisionador."
            )
            app.logger.warning(
                "No se pudo leer el ConfigMap del provisionador: %s",
                e.reason
            )

        except Exception:
            app.logger.exception(
                "Error leyendo el estado del provisionador EC2"
            )
            provisioner_status["status"] = "unavailable"
            provisioner_status["message"] = (
                "Error procesando los datos del provisionador."
            )

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

        # HPA. La biblioteca Kubernetes 34.1.0 puede entregar estas
        # estructuras como diccionarios, por lo que se admite tanto dict
        # como modelos con atributos.
        def obtener_campo(objeto, campo, default=None):
            if isinstance(objeto, dict):
                return objeto.get(campo, default)
            return getattr(objeto, campo, default)

        hpa_data = autoscaling.list_namespaced_horizontal_pod_autoscaler(namespace)
        hpas = []

        for hpa in hpa_data.items:
            current_metrics = []

            for metric in (hpa.status.current_metrics or []):
                metric_type = obtener_campo(metric, "type", "")
                resource_data = obtener_campo(metric, "resource") or {}
                current_data = obtener_campo(resource_data, "current") or {}
                resource_name = obtener_campo(resource_data, "name")

                target_value = None
                for spec_metric in (hpa.spec.metrics or []):
                    spec_type = obtener_campo(spec_metric, "type", "")
                    spec_resource = obtener_campo(spec_metric, "resource") or {}
                    spec_resource_name = obtener_campo(spec_resource, "name")
                    spec_target = obtener_campo(spec_resource, "target") or {}

                    if (
                        spec_type == metric_type
                        and resource_name is not None
                        and spec_resource_name == resource_name
                    ):
                        target_value = obtener_campo(
                            spec_target, "average_utilization"
                        )
                        break

                current_metrics.append({
                    "type": metric_type,
                    "resource": resource_name,
                    "current": obtener_campo(
                        current_data, "average_utilization"
                    ),
                    "target": target_value
                })

            hpas.append({
                "name": hpa.metadata.name,
                "target": hpa.spec.scale_target_ref.name,
                "min_replicas": hpa.spec.min_replicas,
                "max_replicas": hpa.spec.max_replicas,
                "current_replicas": hpa.status.current_replicas or 0,
                "desired_replicas": hpa.status.desired_replicas or 0,
                "current_metrics": current_metrics
            })

        # Eventos recientes. Si la API de eventos falla, no se interrumpe
        # el resto de la monitorización (Pods, métricas, Deployments y HPA).
        events = []

        try:
            events_data = core.list_namespaced_event(
                namespace=namespace,
                limit=30,
                _request_timeout=10
            )

            for event in events_data.items:
                events.append({
                    "type": event.type,
                    "reason": event.reason,
                    "message": event.message,
                    "object": (
                        event.involved_object.name
                        if event.involved_object else None
                    ),
                    "count": event.count,
                    "last_timestamp": (
                        event.last_timestamp.isoformat()
                        if event.last_timestamp
                        else (
                            event.event_time.isoformat()
                            if event.event_time
                            else (
                                event.metadata.creation_timestamp.isoformat()
                                if event.metadata.creation_timestamp
                                else None
                            )
                        )
                    )
                })

            events.sort(
                key=lambda e: e["last_timestamp"] or "",
                reverse=True
            )

        except ApiException as e:
            app.logger.error(
                "No se pudieron consultar los eventos de Kubernetes: "
                "status=%s reason=%s body=%s",
                e.status,
                e.reason,
                e.body
            )
        except Exception:
            app.logger.exception(
                "Error inesperado al procesar eventos de Kubernetes"
            )


        # Nodos del cluster y métricas reales de infraestructura
        def cpu_to_millicores(value):
            if value is None:
                return None
            value = str(value)
            if value.endswith("n"):
                return float(value[:-1]) / 1_000_000
            if value.endswith("u"):
                return float(value[:-1]) / 1_000
            if value.endswith("m"):
                return float(value[:-1])
            return float(value) * 1000

        def memory_to_mib(value):
            if value is None:
                return None

            value = str(value)
            units = {
                "Ki": 1024,
                "Mi": 1024 ** 2,
                "Gi": 1024 ** 3,
                "Ti": 1024 ** 4,
                "K": 1000,
                "M": 1000 ** 2,
                "G": 1000 ** 3,
                "T": 1000 ** 4,
            }

            for suffix, multiplier in units.items():
                if value.endswith(suffix):
                    return (
                        float(value[:-len(suffix)])
                        * multiplier / (1024 ** 2)
                    )

            # Kubernetes también puede devolver cantidades en bytes.
            return float(value) / (1024 ** 2)

        def percentage(usage, allocatable):
            if usage is None or not allocatable:
                return None
            return round((usage / allocatable) * 100, 2)

        # Obtener métricas de CPU y memoria por nodo
        node_metrics_by_name = {}

        try:
            node_metrics_data = custom.list_cluster_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                plural="nodes",
                _request_timeout=10
            )

            for item in node_metrics_data.get("items", []):
                name = item["metadata"]["name"]
                usage = item.get("usage", {})

                node_metrics_by_name[name] = {
                    "cpu_millicores": cpu_to_millicores(
                        usage.get("cpu")
                    ),
                    "memory_mib": memory_to_mib(
                        usage.get("memory")
                    )
                }

        except Exception:
            app.logger.exception(
                "No se pudieron consultar las métricas de los nodos"
            )

        # Consultar nodos registrados en Kubernetes
        nodes_data = core.list_node(_request_timeout=10)
        nodes = []

        for node in nodes_data.items:
            name = node.metadata.name
            labels = node.metadata.labels or {}
            provider_id = node.spec.provider_id or ""

            # Relacionar el nodo con EC2 usando providerID.
            instance_id = None
            if provider_id.startswith("aws:///"):
                instance_id = provider_id.rsplit("/", 1)[-1]

            # El servidor principal utiliza un providerID k3s://.
            # En ese caso, recuperar su EC2 ID desde la etiqueta propia.
            if not instance_id:
                instance_id = labels.get("cloudstockflow/ec2-instance-id")

            # Estado real del nodo
            ready_status = "Unknown"
            for condition in (node.status.conditions or []):
                if condition.type == "Ready":
                    if condition.status == "True":
                        ready_status = "Ready"
                    elif condition.status == "False":
                        ready_status = "NotReady"
                    else:
                        ready_status = "Unknown"
                    break

            addresses = {
                address.type: address.address
                for address in (node.status.addresses or [])
            }

            capacity_data = node.status.capacity or {}
            allocatable_data = node.status.allocatable or {}

            capacity_cpu = cpu_to_millicores(
                capacity_data.get("cpu")
            )
            capacity_memory = memory_to_mib(
                capacity_data.get("memory")
            )

            allocatable_cpu = cpu_to_millicores(
                allocatable_data.get("cpu")
            )
            allocatable_memory = memory_to_mib(
                allocatable_data.get("memory")
            )

            metrics = node_metrics_by_name.get(name)

            cpu_usage = (
                metrics.get("cpu_millicores")
                if metrics else None
            )
            memory_usage = (
                metrics.get("memory_mib")
                if metrics else None
            )

            role = labels.get("cloudstockflow/role")
            if not role:
                if (
                    "node-role.kubernetes.io/control-plane" in labels
                    or "node-role.kubernetes.io/master" in labels
                ):
                    role = "control-plane"
                else:
                    role = "worker"

            availability_zone = (
                labels.get("topology.kubernetes.io/zone")
                or labels.get("failure-domain.beta.kubernetes.io/zone")
            )

            # Los Pods de esta sección pertenecen al namespace default.
            node_pods = [
                pod for pod in pods
                if pod["node"] == name
            ]

            nodes.append({
                "name": name,
                "role": role,
                "status": ready_status,
                "provider_id": provider_id or None,
                "instance_id": instance_id,
                "instance_type": (
                    labels.get("cloudstockflow/instance-type")
                    or labels.get("node.kubernetes.io/instance-type")
                ),
                "availability_zone": availability_zone,
                "private_ip": addresses.get("InternalIP"),
                "external_ip": addresses.get("ExternalIP"),
                "capacity": {
                    "cpu_cores": (
                        round(capacity_cpu / 1000, 3)
                        if capacity_cpu is not None else None
                    ),
                    "memory_mib": (
                        round(capacity_memory, 2)
                        if capacity_memory is not None else None
                    ),
                    "pods": int(capacity_data["pods"])
                    if capacity_data.get("pods") else None
                },
                "allocatable": {
                    "cpu_millicores": allocatable_cpu,
                    "memory_mib": (
                        round(allocatable_memory, 2)
                        if allocatable_memory is not None else None
                    ),
                    "pods": int(allocatable_data["pods"])
                    if allocatable_data.get("pods") else None
                },
                "usage": {
                    "cpu_millicores": (
                        round(cpu_usage, 2)
                        if cpu_usage is not None else None
                    ),
                    "cpu_percent": percentage(
                        cpu_usage, allocatable_cpu
                    ),
                    "memory_mib": (
                        round(memory_usage, 2)
                        if memory_usage is not None else None
                    ),
                    "memory_percent": percentage(
                        memory_usage, allocatable_memory
                    )
                },
                "default_namespace_pod_count": len(node_pods),
                "default_namespace_pods": [
                    pod["name"] for pod in node_pods
                ]
            })

        return jsonify({
            "status": "ok",
            "timestamp": datetime.utcnow().isoformat(),
            "pods": pods,
            "nodes": nodes,
            "pod_metrics": pod_metrics,
            "deployments": deployments,
            "hpas": hpas,
            "events": events,
            "provisioner": provisioner_status
        })

    except ApiException as e:
        app.logger.exception("Error de la API de Kubernetes")

        return jsonify({
            "status": "error",
            "message": "Error consultando Kubernetes",
            "http_status": e.status,
            "details": e.reason,
            "body": e.body
        }), 500

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
