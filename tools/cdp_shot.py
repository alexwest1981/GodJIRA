#!/usr/bin/env python3
"""Skärmdump av en sida via CDP, med väntetid i verklig tid.

chromium --headless --screenshot tar bilden vid load-eventet, alltså innan en SPA
hunnit hämta sitt data. Den här startar sin egen chromium, navigerar, väntar, och
ber om bilden först då. Bara stdlib: en minimal websocket-klient räcker.

    python3 cdp_shot.py <url> <utfil> [sekunder] [bredd] [höjd]
"""
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.request

url, out = sys.argv[1], sys.argv[2]
wait = float(sys.argv[3]) if len(sys.argv) > 3 else 12.0
width = int(sys.argv[4]) if len(sys.argv) > 4 else 1500
height = int(sys.argv[5]) if len(sys.argv) > 5 else 940
PORT = 9333

subprocess.run(["pkill", "-f", "remote-debugging-port=%d" % PORT], capture_output=True)
chrome = subprocess.Popen(
    ["chromium", "--headless=new", "--remote-debugging-port=%d" % PORT,
     "--remote-allow-origins=*", "--no-sandbox", "--no-first-run", "--hide-scrollbars",
     "--force-device-scale-factor=2", "--window-size=%d,%d" % (width, height),
     "--user-data-dir=/tmp/cdp-profile", "--disable-gpu", "about:blank"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

ws_url = ""
for _ in range(60):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % PORT, timeout=2) as answer:
            # /json/version ger *webbläsarens* mål; sidan är ett eget mål.
            pages = [t for t in json.load(answer) if t.get("type") == "page"]
            ws_url = pages[0]["webSocketDebuggerUrl"]
        break
    except Exception:
        time.sleep(0.5)
if not ws_url:
    print("chromium svarade inte på fjärrporten")
    chrome.terminate()
    raise SystemExit(1)

hostport = ws_url.split("//", 1)[1].split("/", 1)[0]
host, port = hostport.rsplit(":", 1)
sock = socket.create_connection((host, int(port)), timeout=120)
key = base64.b64encode(os.urandom(16)).decode()
sock.sendall(("GET /devtools/page/%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n"
              "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n"
              "Origin: http://127.0.0.1\r\n\r\n"
              % (ws_url.rstrip("/").rsplit("/", 1)[1], hostport, key)).encode())
handshake = sock.recv(4096)
if b" 101" not in handshake.split(b"\r\n")[0]:
    print("websocket-handskakningen gick fel:", handshake[:120])
    chrome.terminate()
    raise SystemExit(1)
leftover = handshake.split(b"\r\n\r\n", 1)[1]


class Ws:
    """Minimal klient: maskerade textramar ut, omsamlade ramar in."""

    def __init__(self, sock, buffered=b""):
        self.sock, self.buf, self.pending = sock, bytearray(buffered), bytearray()

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("anslutningen stängdes")
            self.buf += chunk
        out, self.buf = bytes(self.buf[:n]), self.buf[n:]
        return out

    def send(self, text):
        payload = text.encode()
        header = bytearray([0x81])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = os.urandom(4)
        header += mask
        self.sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def recv(self):
        while True:
            first, second = self._read(2)
            fin, opcode = first & 0x80, first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read(8))[0]
            if second & 0x80:
                mask = self._read(4)
            else:
                mask = None
            payload = self._read(length)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x9:                      # ping: svara och läs vidare
                self.sock.sendall(bytes([0x8A, 0x80]) + b"\x00\x00\x00\x00")
                continue
            if opcode == 0x8:
                raise RuntimeError("servern stängde")
            self.pending += payload
            if fin:
                out, self.pending = bytes(self.pending), bytearray()
                return out


ws = Ws(sock, leftover)
counter = [0]


def call(method, **params):
    counter[0] += 1
    ws.send(json.dumps({"id": counter[0], "method": method, "params": params}))
    while True:
        message = json.loads(ws.recv())
        if message.get("id") == counter[0]:
            return message.get("result", message)


call("Emulation.setDeviceMetricsOverride", width=width, height=height,
     deviceScaleFactor=2, mobile=False)
call("Page.navigate", url=url)
# Vänta på att appen själv säger att den är klar (splash-fönstret får klassen "gone"),
# i stället för att gissa en tid: mocken startar flera underprocesser och första
# anropet efter en omstart tar ibland tio sekunder.
deadline = time.time() + wait
while time.time() < deadline:
    time.sleep(1)
    try:
        ready = call("Runtime.evaluate", returnByValue=True,
                     expression='(function(){var s=document.getElementById("splash");'
                                'return !!s && s.classList.contains("gone");})()')
    except Exception:
        break
    if ready.get("result", {}).get("value") is True:
        break
if os.environ.get("EVAL"):
    answer = call("Runtime.evaluate", expression=os.environ["EVAL"], returnByValue=True,
                  awaitPromise=False)
    print(json.dumps(answer.get("result", {}).get("value"), ensure_ascii=False)[:1500])
    chrome.terminate()
    raise SystemExit(0)
shot = call("Page.captureScreenshot", format="png")
with open(out, "wb") as handle:
    handle.write(base64.b64decode(shot["data"]))
chrome.terminate()
print("{}: {} byte".format(out, os.path.getsize(out)))
