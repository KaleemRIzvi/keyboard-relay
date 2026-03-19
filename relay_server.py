#!/usr/bin/env python3
"""
relay_server.py - Central relay server
Routes traffic between MULTIPLE agents and a single controller.
"""

import socket, ssl, threading, os, time, json

HOST   = "0.0.0.0"
PORT   = int(os.environ.get("PORT", 55000))
SECRET = os.environ.get("RELAY_SECRET", "mysecret99")
CERT_FILE = os.environ.get("CERT_FILE", "")
KEY_FILE  = os.environ.get("KEY_FILE",  "")

agents: dict = {}          # { device_id: conn }
agents_last_seen: dict = {}  # { device_id: timestamp }
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
        return buf.split(b"\n", 1)[0].decode("utf-8").strip()
    except Exception as e:
        log(f"[recv_line error] {e}")
        return None
    finally:
        conn.settimeout(None)


def _push_agent_list():
    """Send current agent list to controller. Call WITHOUT holding conn_lock."""
    with conn_lock:
        ctrl = controller_conn
        devices = list(agents.keys())
    if ctrl:
        send_json(ctrl, {"type": "agent_list", "devices": devices})


def handle_client(conn, addr):
    global controller_conn
    log(f"New connection from {addr[0]}:{addr[1]}")
    role = None
    device_id = None
    try:
        raw = recv_line(conn, timeout=15)
        if raw is None:
            log(f"No auth from {addr[0]}"); return
        try:
            msg = json.loads(raw)
        except Exception:
            send_json(conn, {"status": "error", "msg": "Invalid JSON"}); return

        if msg.get("secret") != SECRET:
            send_json(conn, {"status": "error", "msg": "Wrong secret"}); return

        role = msg.get("role")
        if role not in ("agent", "controller"):
            send_json(conn, {"status": "error", "msg": "Bad role"}); return

        if role == "agent":
            device_id = msg.get("device_id") or addr[0]
            with conn_lock:
                if device_id in agents:
                    try: agents[device_id].close()
                    except: pass
                    log(f"Agent '{device_id}' replaced")
                agents[device_id] = conn
                agents_last_seen[device_id] = time.time()
            log(f"Agent '{device_id}' online")
            send_json(conn, {"status": "ok", "role": "agent", "device_id": device_id})
            # Notify controller AFTER releasing conn_lock (no deadlock)
            _push_agent_list()

        else:  # controller
            with conn_lock:
                if controller_conn:
                    try: controller_conn.close()
                    except: pass
                controller_conn = conn
                devices = list(agents.keys())
            log(f"Controller connected ({len(devices)} agent(s) online)")
            send_json(conn, {"status": "ok", "role": "controller"})
            # Send agent list as a separate message — picked up by main recv loop
            send_json(conn, {"type": "agent_list", "devices": devices})

        # ── relay loop ──────────────────────────────────────────────────────
        buffer = ""
        while True:
            try:
                data = conn.recv(65536)
                if not data: break
                buffer += data.decode("utf-8", errors="replace")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line: continue
                    try:
                        parsed = json.loads(line)
                    except Exception:
                        continue
                    # Determine target connection WITHOUT holding the lock during send
                    target_conn = None
                    with conn_lock:
                        if role == "agent":
                            parsed["device_id"] = device_id
                            agents_last_seen[device_id] = time.time()
                            target_conn = controller_conn
                        elif role == "controller":
                            target = parsed.get("target_device")
                            if target and target in agents:
                                target_conn = agents[target]
                    if target_conn:
                        send_json(target_conn, parsed)
            except Exception as e:
                log(f"Relay error ({role}): {e}"); break

    except Exception as e:
        log(f"Handler error: {e}")
    finally:
        with conn_lock:
            if role == "agent" and device_id and agents.get(device_id) is conn:
                del agents[device_id]
                agents_last_seen.pop(device_id, None)
                log(f"Agent '{device_id}' offline")
            elif role == "controller" and controller_conn is conn:
                controller_conn = None
                log("Controller disconnected")
        try: conn.close()
        except: pass
        if role == "agent":
            _push_agent_list()


def _heartbeat_watchdog():
    """Remove agents that have gone silent (WiFi drop without clean disconnect)."""
    TIMEOUT = 60  # seconds
    while True:
        time.sleep(30)
        now = time.time()
        dead = []
        with conn_lock:
            for dev, ts in list(agents_last_seen.items()):
                if now - ts > TIMEOUT:
                    dead.append(dev)
        for dev in dead:
            log(f"Agent '{dev}' heartbeat timeout — removing")
            with conn_lock:
                conn = agents.pop(dev, None)
                agents_last_seen.pop(dev, None)
            if conn:
                try: conn.close()
                except: pass
            _push_agent_list()


def main():
    log(f"Relay starting on {HOST}:{PORT}  TLS={'ON' if CERT_FILE and KEY_FILE else 'OFF'}")
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(20)
    if CERT_FILE and KEY_FILE:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(CERT_FILE, KEY_FILE)
        srv = ctx.wrap_socket(srv, server_side=True)
        log("TLS ready")
    threading.Thread(target=_heartbeat_watchdog, daemon=True).start()
    log("Ready — waiting for connections")
    while True:
        try:
            conn, addr = srv.accept()
            threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()
        except Exception as e:
            log(f"Accept error: {e}")


if __name__ == "__main__":
    main()
