#!/usr/bin/env python3
"""
relay_server.py - Central relay server (host on Railway / VPS)
Routes traffic between MULTIPLE agents and a single controller.
Each agent identifies itself by hostname — reconnects replace the old slot.
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
SECRET = os.environ.get("RELAY_SECRET", "mysecret99")

CERT_FILE = os.environ.get("CERT_FILE", "")
KEY_FILE  = os.environ.get("KEY_FILE",  "")

# { device_id: conn }
agents: dict = {}
controller_conn = None
conn_lock = threading.Lock()


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


def notify_controller_agent_list():
    """Push current online device list to controller."""
    with conn_lock:
        if controller_conn:
            device_list = list(agents.keys())
            send_json(controller_conn, {"type": "agent_list", "devices": device_list})


def handle_client(conn, addr):
    global controller_conn
    log(f"New connection from {addr[0]}:{addr[1]}")
    role = None
    device_id = None
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
            send_json(conn, {"status": "error", "msg": "Bad role"})
            return

        if role == "agent":
            device_id = msg.get("device_id") or addr[0]
            with conn_lock:
                if device_id in agents:
                    try:
                        agents[device_id].close()
                    except Exception:
                        pass
                    log(f"Agent '{device_id}' replaced (reconnect)")
                agents[device_id] = conn
            log(f"Agent '{device_id}' connected from {addr[0]}")
            send_json(conn, {"status": "ok", "role": "agent", "device_id": device_id,
                             "msg": f"Connected as agent '{device_id}'"})
            notify_controller_agent_list()

        else:  # controller
            with conn_lock:
                if controller_conn:
                    try:
                        controller_conn.close()
                    except Exception:
                        pass
                controller_conn = conn
            log(f"Controller connected from {addr[0]}")
            with conn_lock:
                device_list = list(agents.keys())
            send_json(conn, {"status": "ok", "role": "controller",
                             "msg": "Connected as controller",
                             "devices": device_list})

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
                        if role == "agent":
                            parsed["device_id"] = device_id
                            if controller_conn:
                                send_json(controller_conn, parsed)
                        elif role == "controller":
                            target = parsed.get("target_device")
                            if target and target in agents:
                                send_json(agents[target], parsed)
                            else:
                                log(f"[Relay] Unknown target '{target}' — dropping")

            except Exception as e:
                log(f"Relay error ({role}/{device_id}): {e}")
                break

    except Exception as e:
        log(f"Client handler error: {e}")
    finally:
        with conn_lock:
            if role == "agent" and device_id and agents.get(device_id) is conn:
                del agents[device_id]
                log(f"Agent '{device_id}' disconnected")
            elif role == "controller" and controller_conn is conn:
                controller_conn = None
                log("Controller disconnected")
        try:
            conn.close()
        except Exception:
            pass
        if role == "agent":
            notify_controller_agent_list()


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
    raw_server.listen(20)

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
