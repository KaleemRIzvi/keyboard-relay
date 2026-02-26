#!/usr/bin/env python3
"""
relay_server.py - Deploy this on Railway.app
Fixed version with detailed logging to help debug connection issues.
"""

import socket
import threading
import os
import time
import json

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", 55000))
SECRET = os.environ.get("RELAY_SECRET", "changeme123")

damaged_conn = None
controller_conn = None
conn_lock = threading.Lock()

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def send_json(conn, data: dict):
    try:
        msg = json.dumps(data) + "\n"
        conn.sendall(msg.encode("utf-8"))
        return True
    except Exception as e:
        log(f"[send_json error] {e}")
        return False

def recv_line(conn, timeout=15):
    """Read until newline with timeout."""
    conn.settimeout(timeout)
    buf = b""
    try:
        while b"\n" not in buf:
            chunk = conn.recv(1)
            if not chunk:
                return None
            buf += chunk
        return buf.decode("utf-8").strip()
    except Exception as e:
        log(f"[recv_line error] {e}")
        return None
    finally:
        conn.settimeout(None)

def handle_client(conn, addr):
    global damaged_conn, controller_conn
    log(f"New connection from {addr[0]}:{addr[1]}")
    role = None

    try:
        # Read auth line
        raw = recv_line(conn, timeout=15)
        if raw is None:
            log(f"No auth received from {addr[0]} - closing")
            return

        log(f"Auth received from {addr[0]}: {raw[:80]}")

        try:
            msg = json.loads(raw)
        except Exception as e:
            log(f"JSON parse error from {addr[0]}: {e} | raw: {repr(raw)}")
            send_json(conn, {"status": "error", "msg": "Invalid JSON"})
            return

        if msg.get("secret") != SECRET:
            log(f"Wrong secret from {addr[0]}. Got: {repr(msg.get('secret'))} Expected: {repr(SECRET)}")
            send_json(conn, {"status": "error", "msg": "Wrong secret"})
            return

        role = msg.get("role")
        if role not in ("damaged", "controller"):
            send_json(conn, {"status": "error", "msg": "Role must be damaged or controller"})
            return

        with conn_lock:
            if role == "damaged":
                if damaged_conn:
                    try: damaged_conn.close()
                    except: pass
                damaged_conn = conn
                log(f"Damaged laptop connected from {addr[0]}")
            else:
                if controller_conn:
                    try: controller_conn.close()
                    except: pass
                controller_conn = conn
                log(f"Controller connected from {addr[0]}")

        send_json(conn, {"status": "ok", "role": role, "msg": f"Connected as {role}"})
        log(f"Auth OK for {role} @ {addr[0]}")

        # Relay loop
        buffer = ""
        while True:
            try:
                data = conn.recv(4096)
                if not data:
                    break
                buffer += data.decode("utf-8")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        parsed = json.loads(line)
                    except:
                        continue

                    with conn_lock:
                        if role == "damaged" and controller_conn:
                            send_json(controller_conn, parsed)
                        elif role == "controller" and damaged_conn:
                            send_json(damaged_conn, parsed)
            except Exception as e:
                log(f"Relay loop error ({role}): {e}")
                break

    except Exception as e:
        log(f"handle_client error ({role or 'unknown'} @ {addr[0]}): {e}")
    finally:
        with conn_lock:
            if role == "damaged" and damaged_conn is conn:
                damaged_conn = None
                log("Damaged laptop disconnected")
            elif role == "controller" and controller_conn is conn:
                controller_conn = None
                log("Controller disconnected")
        try:
            conn.close()
        except:
            pass

def main():
    log(f"Starting relay server on {HOST}:{PORT}")
    log(f"RELAY_SECRET is {'SET' if SECRET != 'changeme123' else 'NOT SET - using default!'}")
    log(f"Secret first 3 chars: {SECRET[:3]}...")

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen(10)
    log(f"Relay server ready and listening on port {PORT}")

    while True:
        try:
            conn, addr = server.accept()
            t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            t.start()
        except Exception as e:
            log(f"Accept error: {e}")

if __name__ == "__main__":
    main()
