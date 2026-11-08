
import os
import math
import socket
import sqlite3

import psycopg2
from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


app = Flask(__name__)
CORS(app)

MODO_K8S = os.environ.get("DB_HOST") is not None


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


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000
    )
