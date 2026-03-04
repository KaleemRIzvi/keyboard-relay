#!/usr/bin/env python3
"""
relay_server.py - Central relay server (host on Railway / VPS)
Routes all traffic between the damaged laptop (agent) and controller (GUI).
Supports TLS if CERT_FILE and KEY_FILE env vars are set.
"""

import socket
import ssl
import threading
import os
import time
import json

HOST   = "0.0.0.0"
PORT   = int(os.environ.get("PORT", 55000))
SECRET = os.environ.get("RELAY_SECRET", "mysecret99")  # ← change to mysecret99

# Optional TLS (set env vars CERT_FILE and KEY_FILE on your server)
CERT_FILE = os.environ.get("CERT_FILE", "")
KEY_FILE  = os.environ.get("KEY_FILE",  "")

agent_conn      = None
controller_conn = None
conn_lock       = threading.Lock()


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def send_json(conn, data):
    try:
        conn.sendall((json.dumps(data) + "\n").encode("utf-8"))
        return True
    except Exception as e:
        log(f"[send_json error] {e}")
        return False


def recv_line(conn, timeout=15):
    conn.settimeout(timeout)
    buf = b""
    try:
        while b"\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                return None
            buf += chunk
        line = buf.split(b"\n", 1)[0]
        return line.decode("utf-8").strip()
    except Exception as e:
        log(f"[recv_line error] {e}")
        return None
    finally:
        conn.settimeout(None)


def handle_client(conn, addr):
    global agent_conn, controller_conn
    log(f"New connection from {addr[0]}:{addr[1]}")
    role = None
    try:
        raw = recv_line(conn, timeout=15)
        if raw is None:
            log(f"No auth from {addr[0]}")
            return
        try:
            msg = json.loads(raw)
        except Exception as e:
            log(f"Bad JSON from {addr[0]}: {e}")
            send_json(conn, {"status": "error", "msg": "Invalid JSON"})
            return

        if msg.get("secret") != SECRET:
            log(f"Wrong secret from {addr[0]}")
            send_json(conn, {"status": "error", "msg": "Wrong secret"})
            return

        role = msg.get("role")
        if role not in ("agent", "controller"):
            send_json(conn, {"status": "error", "msg": "Bad role — use 'agent' or 'controller'"})
            return

        with conn_lock:
            if role == "agent":
                if agent_conn:
                    try: agent_conn.close()
                    except: pass
                agent_conn = conn
                log(f"Agent (damaged laptop) connected from {addr[0]}")
            else:
                if controller_conn:
                    try: controller_conn.close()
                    except: pass
                controller_conn = conn
                log(f"Controller connected from {addr[0]}")

        send_json(conn, {"status": "ok", "role": role, "msg": f"Connected as {role}"})
        log(f"Auth OK for {role} @ {addr[0]}")

        buffer = ""
        while True:
            try:
                data = conn.recv(65536)
                if not data:
                    break
                buffer += data.decode("utf-8", errors="replace")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        parsed = json.loads(line)
                    except Exception:
                        continue
                    with conn_lock:
                        if role == "agent" and controller_conn:
                            send_json(controller_conn, parsed)
                        elif role == "controller" and agent_conn:
                            send_json(agent_conn, parsed)
            except Exception as e:
                log(f"Relay error ({role}): {e}")
                break

    except Exception as e:
        log(f"Client handler error: {e}")
    finally:
        with conn_lock:
            if role == "agent" and agent_conn is conn:
                agent_conn = None
                log("Agent disconnected")
            elif role == "controller" and controller_conn is conn:
                controller_conn = None
                log("Controller disconnected")
        try: conn.close()
        except: pass


def main():
    log("=== Relay Server Starting ===")
    log(f"Binding to {HOST}:{PORT}")
    log(f"TLS: {'ENABLED' if CERT_FILE and KEY_FILE else 'DISABLED (plaintext)'}")

    raw_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        raw_server.bind((HOST, PORT))
    except Exception as e:
        log(f"BIND FAILED: {e}")
        return
    raw_server.listen(10)

    if CERT_FILE and KEY_FILE:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(CERT_FILE, KEY_FILE)
        server = ctx.wrap_socket(raw_server, server_side=True)
        log("TLS context loaded successfully")
    else:
        server = raw_server

    log(f"=== Ready! Listening on {HOST}:{PORT} ===")
    while True:
        try:
            conn, addr = server.accept()
            t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            t.start()
        except Exception as e:
            log(f"Accept error: {e}")


if __name__ == "__main__":
    main()
