#!/usr/bin/env python3
"""
relay_server.py - Railway cloud relay
Fixes:
- Large buffer (65536) for screenshots
- Keepalive ping every 30s to prevent Railway sleep
- Better disconnect/reconnect handling
- Multiple controllers supported
"""
import socket
import threading
import os
import time
import json

HOST   = "0.0.0.0"
PORT   = int(os.environ.get("PORT", 55000))
SECRET = os.environ.get("RELAY_SECRET", "mysecret99")

damaged_conn    = None
controller_conn = None
conn_lock       = threading.Lock()

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def send_json(conn, data):
    try:
        conn.sendall((json.dumps(data) + "\n").encode("utf-8"))
        return True
    except:
        return False

def recv_line(conn, timeout=20):
    conn.settimeout(timeout)
    buf = b""
    try:
        while b"\n" not in buf:
            chunk = conn.recv(1)
            if not chunk:
                return None
            buf += chunk
        return buf.decode("utf-8").strip()
    except:
        return None
    finally:
        conn.settimeout(None)

def keepalive_ping():
    """Send ping every 30s to keep Railway from sleeping and detect dead connections."""
    while True:
        time.sleep(30)
        with conn_lock:
            for role, conn in [("damaged", damaged_conn), ("controller", controller_conn)]:
                if conn:
                    try:
                        conn.sendall(b'{"ping":1}\n')
                    except:
                        log(f"Keepalive failed for {role} — will be cleaned up on next recv")

def handle_client(conn, addr):
    global damaged_conn, controller_conn
    log(f"New connection from {addr[0]}:{addr[1]}")
    role = None
    try:
        # Auth
        raw = recv_line(conn, timeout=20)
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
        if role not in ("damaged", "controller"):
            send_json(conn, {"status": "error", "msg": "Bad role"})
            return

        with conn_lock:
            if role == "damaged":
                if damaged_conn:
                    try: damaged_conn.close()
                    except: pass
                damaged_conn = conn
                log(f"Damaged laptop connected: {addr[0]}")
            else:
                if controller_conn:
                    try: controller_conn.close()
                    except: pass
                controller_conn = conn
                log(f"Controller connected: {addr[0]}")

        send_json(conn, {"status": "ok", "role": role, "msg": f"Connected as {role}"})
        log(f"Auth OK — {role} from {addr[0]}")

        # Relay loop — large recv buffer for screenshots
        buffer = ""
        conn.settimeout(120)  # 2 min timeout — keepalive will reset this

        while True:
            try:
                data = conn.recv(65536)  # large buffer for screenshot chunks
                if not data:
                    break
                buffer += data.decode("utf-8", errors="replace")

                # Process all complete lines
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        parsed = json.loads(line)
                    except:
                        # Incomplete JSON — put back and wait for more data
                        buffer = line + "\n" + buffer
                        break

                    # Skip pings — don't relay them
                    if "ping" in parsed:
                        continue

                    # Relay to the other side
                    with conn_lock:
                        target = controller_conn if role == "damaged" else damaged_conn
                    if target:
                        ok = send_json(target, parsed)
                        if not ok:
                            log(f"Failed to relay to {'controller' if role=='damaged' else 'damaged'}")

            except socket.timeout:
                # Send keepalive
                try:
                    conn.sendall(b'{"ping":1}\n')
                except:
                    break
            except Exception as e:
                log(f"Relay error ({role}): {e}")
                break

    except Exception as e:
        log(f"Client error from {addr[0]}: {e}")
    finally:
        with conn_lock:
            if role == "damaged" and damaged_conn is conn:
                damaged_conn = None
                log("Damaged laptop disconnected")
            elif role == "controller" and controller_conn is conn:
                controller_conn = None
                log("Controller disconnected")
        try: conn.close()
        except: pass

def main():
    log("=== Relay Server Starting ===")
    log(f"Binding to {HOST}:{PORT}")
    log(f"SECRET: '{SECRET[:3]}...'")

    # Start keepalive thread
    kt = threading.Thread(target=keepalive_ping, daemon=True)
    kt.start()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

    try:
        server.bind((HOST, PORT))
    except Exception as e:
        log(f"BIND FAILED: {e}")
        return

    server.listen(10)
    log(f"=== Ready! Listening on {HOST}:{PORT} ===")

    while True:
        try:
            conn, addr = server.accept()
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            t.start()
        except Exception as e:
            log(f"Accept error: {e}")

if __name__ == "__main__":
    main()
