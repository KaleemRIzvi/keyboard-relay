#!/usr/bin/env python3
"""
relay_server.py — Central relay (host on Railway / VPS)
Supports multiple agents simultaneously.
Each agent registers with a unique AGENT_ID.
The controller receives a live list of online/offline agents
and routes commands to a specific agent using "target" field.
"""

import socket, ssl, threading, os, time, json

HOST   = "0.0.0.0"
PORT   = int(os.environ.get("PORT", 55000))
SECRET = os.environ.get("RELAY_SECRET", "mysecret99")

CERT_FILE = os.environ.get("CERT_FILE", "")
KEY_FILE  = os.environ.get("KEY_FILE",  "")

# ── state ─────────────────────────────────────────────────────────────────────
agents     = {}   # { agent_id: {"conn": sock, "sysinfo": {}, "addr": str} }
controller_conn = None
conn_lock  = threading.Lock()


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


def broadcast_agent_list():
    """Send updated agent list to controller whenever anything changes."""
    with conn_lock:
        if not controller_conn:
            return
        agent_list = [
            {"id": aid, "addr": info["addr"], "sysinfo": info.get("sysinfo", {})}
            for aid, info in agents.items()
        ]
        send_json(controller_conn, {"type": "agent_list", "agents": agent_list})


def handle_agent(conn, addr, agent_id, sysinfo):
    global agents
    log(f"[Agent] '{agent_id}' connected from {addr[0]}")
    with conn_lock:
        if agent_id in agents:
            try: agents[agent_id]["conn"].close()
            except: pass
        agents[agent_id] = {"conn": conn, "addr": addr[0], "sysinfo": sysinfo}
    broadcast_agent_list()
    with conn_lock:
        if controller_conn:
            send_json(controller_conn, {
                "type": "agent_online",
                "id": agent_id,
                "sysinfo": sysinfo,
                "addr": addr[0]
            })
    # relay loop: agent → controller
    buffer = ""
    try:
        while True:
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
                    # stamp the source agent_id so controller knows who sent it
                    parsed["_from"] = agent_id
                    with conn_lock:
                        if controller_conn:
                            send_json(controller_conn, parsed)
                    # update sysinfo cache
                    if parsed.get("type") == "sysinfo":
                        with conn_lock:
                            if agent_id in agents:
                                agents[agent_id]["sysinfo"] = parsed.get("info", {})
                except Exception:
                    continue
    except Exception as e:
        log(f"[Agent relay error '{agent_id}'] {e}")
    finally:
        with conn_lock:
            if agent_id in agents and agents[agent_id]["conn"] is conn:
                del agents[agent_id]
        log(f"[Agent] '{agent_id}' disconnected")
        with conn_lock:
            if controller_conn:
                send_json(controller_conn, {"type": "agent_offline", "id": agent_id})
        broadcast_agent_list()
        try: conn.close()
        except: pass


def handle_controller(conn, addr):
    global controller_conn
    log(f"[Controller] connected from {addr[0]}")
    with conn_lock:
        if controller_conn:
            try: controller_conn.close()
            except: pass
        controller_conn = conn
    # immediately send the current agent list
    broadcast_agent_list()
    buffer = ""
    try:
        while True:
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
                    target = parsed.get("target")   # controller sets this
                    if target:
                        with conn_lock:
                            agent_info = agents.get(target)
                        if agent_info:
                            send_json(agent_info["conn"], parsed)
                        else:
                            send_json(conn, {"type": "error", "msg": f"Agent '{target}' not online"})
                    else:
                        # broadcast to all agents (e.g. ping)
                        with conn_lock:
                            for info in agents.values():
                                send_json(info["conn"], parsed)
                except Exception:
                    continue
    except Exception as e:
        log(f"[Controller relay error] {e}")
    finally:
        with conn_lock:
            if controller_conn is conn:
                controller_conn = None
        log("[Controller] disconnected")
        try: conn.close()
        except: pass


def handle_client(conn, addr):
    raw = recv_line(conn, timeout=15)
    if not raw:
        log(f"No auth from {addr[0]}")
        conn.close()
        return
    try:
        msg = json.loads(raw)
    except Exception:
        send_json(conn, {"status": "error", "msg": "Invalid JSON"})
        conn.close()
        return

    if msg.get("secret") != SECRET:
        send_json(conn, {"status": "error", "msg": "Wrong secret"})
        conn.close()
        return

    role = msg.get("role")
    if role == "agent":
        agent_id = msg.get("id", addr[0])   # fallback to IP if no id
        sysinfo  = msg.get("sysinfo", {})
        send_json(conn, {"status": "ok", "role": "agent", "msg": f"Connected as agent '{agent_id}'"})
        handle_agent(conn, addr, agent_id, sysinfo)
    elif role == "controller":
        send_json(conn, {"status": "ok", "role": "controller", "msg": "Connected as controller"})
        handle_controller(conn, addr)
    else:
        send_json(conn, {"status": "error", "msg": "Bad role"})
        conn.close()


def main():
    log("=== Multi-Agent Relay Server Starting ===")
    log(f"Binding to {HOST}:{PORT}")
    log(f"TLS: {'ENABLED' if CERT_FILE and KEY_FILE else 'DISABLED'}")

    raw_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    raw_server.bind((HOST, PORT))
    raw_server.listen(20)

    if CERT_FILE and KEY_FILE:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(CERT_FILE, KEY_FILE)
        server = ctx.wrap_socket(raw_server, server_side=True)
        log("TLS loaded successfully")
    else:
        server = raw_server

    log(f"=== Ready — listening on port {PORT} ===")
    while True:
        try:
            conn, addr = server.accept()
            threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()
        except Exception as e:
            log(f"Accept error: {e}")


if __name__ == "__main__":
    main()
