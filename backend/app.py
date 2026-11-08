import os
import math
import socket
import sqlite3
import psycopg2
from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime

app = Flask(__name__)
CORS(app)

MODO_K8S = os.environ.get('DB_HOST') is not None

def get_db_connection():
    if MODO_K8S:
        conn = psycopg2.connect(
            host=os.environ.get('DB_HOST'),
            database=os.environ.get('DB_NAME', 'banco_db'),
            user=os.environ.get('DB_USER', 'postgres'),
            password=os.environ.get('DB_PASSWORD', 'password')
        )
    else:
        conn = sqlite3.connect('banco_local.db', check_same_thread=False)
        conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Crear tablas si no existen
    if MODO_K8S:
        cur.execute('''CREATE TABLE IF NOT EXISTS cuenta (id SERIAL PRIMARY KEY, titular VARCHAR(100), saldo NUMERIC)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS transacciones (id SERIAL PRIMARY KEY, tipo VARCHAR(50), monto NUMERIC, cuenta_destino VARCHAR(50), fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        cur.execute('SELECT COUNT(*) FROM cuenta')
        if cur.fetchone()[0] == 0:
            cur.execute("INSERT INTO cuenta (titular, saldo) VALUES ('Usuario Demo', 15000.00)")
    else:
        cur.execute('''CREATE TABLE IF NOT EXISTS cuenta (id INTEGER PRIMARY KEY, titular TEXT, saldo REAL)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS transacciones (id INTEGER PRIMARY KEY, tipo TEXT, monto REAL, cuenta_destino TEXT, fecha DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        cur.execute('SELECT COUNT(*) FROM cuenta')
        if cur.fetchone()[0] == 0:
            cur.execute("INSERT INTO cuenta (titular, saldo) VALUES ('Usuario Demo', 15000.00)")
    
    conn.commit()
    cur.close()
    conn.close()

# Inicializar BD al arrancar
init_db()

@app.route('/api/cuenta', methods=['GET'])
def obtener_cuenta():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Obtener saldo
    cur.execute('SELECT saldo FROM cuenta WHERE id = 1')
    saldo = cur.fetchone()[0]
    
    # Obtener ultimas 5 transacciones
    if MODO_K8S:
        cur.execute('SELECT tipo, monto, cuenta_destino, TO_CHAR(fecha, \'YYYY-MM-DD HH24:MI\') FROM transacciones ORDER BY id DESC LIMIT 5')
    else:
        cur.execute('SELECT tipo, monto, cuenta_destino, datetime(fecha, "localtime") FROM transacciones ORDER BY id DESC LIMIT 5')
    
    transacciones = [{"tipo": row[0], "monto": float(row[1]), "destino": row[2], "fecha": row[3]} for row in cur.fetchall()]
    
    cur.close()
    conn.close()
    
    return jsonify({
        "saldo": float(saldo),
        "movimientos": transacciones,
        "pod": socket.gethostname(),
        "motor_db": "PostgreSQL" if MODO_K8S else "SQLite Local"
    })

@app.route('/api/transfer', methods=['POST'])
def transferir():
    datos = request.json
    monto = float(datos.get('monto', 0))
    destino = datos.get('destino', 'Desconocido')
    
    if monto <= 0:
        return jsonify({"error": "Monto inválido"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # Verificar saldo
        cur.execute('SELECT saldo FROM cuenta WHERE id = 1')
        saldo_actual = float(cur.fetchone()[0])
        
        if saldo_actual < monto:
            return jsonify({"error": "Fondos insuficientes"}), 400
            
        # Restar saldo
        nuevo_saldo = saldo_actual - monto
        cur.execute('UPDATE cuenta SET saldo = ? WHERE id = 1' if not MODO_K8S else 'UPDATE cuenta SET saldo = %s WHERE id = 1', (nuevo_saldo,))
        
        # Registrar transacción
        cur.execute('INSERT INTO transacciones (tipo, monto, cuenta_destino) VALUES (?, ?, ?)' if not MODO_K8S else 'INSERT INTO transacciones (tipo, monto, cuenta_destino) VALUES (%s, %s, %s)', 
                    ('Transferencia enviada', monto, destino))
        
        conn.commit()
        return jsonify({"mensaje": "Transferencia exitosa", "nuevo_saldo": nuevo_saldo, "pod": socket.gethostname()})
    
    except Exception as e:
        conn.rollback()
        return jsonify({"error": "Fallo en transacción"}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/api/stress', methods=['GET'])
def stress():
    count = 0
    for i in range(15000000):
        count += math.sqrt(i)
    return jsonify({"mensaje": "Pico de CPU finalizado", "pod": socket.gethostname()})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)