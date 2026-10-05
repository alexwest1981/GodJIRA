#!/usr/bin/env python3
"""Mäter importens filväg på riktigt: välj en fil i webbläsaren och läs vad panelen gör.

Symptomet Alex rapporterade ("det går inte att ladda upp en fil i importen") sitter i
gränssnittet, så det måste mätas i gränssnittet. Att sätta inputens filer går bara via
CDP (DOM.setFileInputFiles) -- det är samma väg en riktig filväljare tar, alltså en
riktig ändringshändelse, inte en påhittad.
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

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8788/#import"
FIL = sys.argv[2] if len(sys.argv) > 2 else "/tmp/bokning.md"
PORT = 9344


subprocess.run(["pkill", "-f", "user-data-dir=/tmp/imp-probe"], capture_output=True)
chrome = subprocess.Popen(
    ["chromium", "--headless=new", "--remote-debugging-port=%d" % PORT,
     "--remote-allow-origins=*", "--no-sandbox", "--no-first-run", "--disable-gpu",
     "--user-data-dir=/tmp/imp-probe", "about:blank"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

ws_url = ""
for _ in range(60):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % PORT, timeout=2) as answer:
            pages = [t for t in json.load(answer) if t.get("type") == "page"]
            ws_url = pages[0]["webSocketDebuggerUrl"]
        break
    except Exception:
        time.sleep(0.5)
if not ws_url:
    print("chromium svarade inte")
    chrome.terminate()
    raise SystemExit(1)

hostport = ws_url.split("//", 1)[1].split("/", 1)[0]
host, port = hostport.rsplit(":", 1)
sock = socket.create_connection((host, int(port)), timeout=60)
key = base64.b64encode(os.urandom(16)).decode()
sock.sendall(("GET /devtools/page/%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n"
              "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n"
              "Origin: http://127.0.0.1\r\n\r\n"
              % (ws_url.rstrip("/").rsplit("/", 1)[1], hostport, key)).encode())
handshake = sock.recv(4096)
leftover = handshake.split(b"\r\n\r\n", 1)[1]


class Ws:
    def __init__(self, sock, buffered=b""):
        self.sock, self.buf = sock, bytearray(buffered)

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("stängd")
            self.buf += chunk
        out, self.buf = bytes(self.buf[:n]), self.buf[n:]
        return out

    def send(self, text):
        payload = text.encode()
        header = bytearray([0x81])
        if len(payload) < 126:
            header.append(0x80 | len(payload))
        elif len(payload) < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", len(payload))
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", len(payload))
        mask = os.urandom(4)
        header += mask
        self.sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def recv(self):
        while True:
            first, second = self._read(2)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read(8))[0]
            if second & 0x80:
                self._read(4)
            payload = self._read(length)
            if first & 0x0F == 1:
                return payload.decode()
            if first & 0x0F == 8:
                raise RuntimeError("stängd")


ws = Ws(sock, leftover)
n = [0]


def cdp(method, **params):
    n[0] += 1
    ws.send(json.dumps({"id": n[0], "method": method, "params": params}))
    while True:
        rad = json.loads(ws.recv())
        if rad.get("id") == n[0]:
            if "error" in rad:
                raise RuntimeError(method + ": " + str(rad["error"])[:200])
            return rad.get("result", {})


def js(uttryck):
    r = cdp("Runtime.evaluate", expression=uttryck, returnByValue=True, awaitPromise=True)
    return (r.get("result") or {}).get("value")


cdp("Page.enable")
cdp("Page.navigate", url=URL)
klar = False
for _ in range(60):
    time.sleep(2)
    if js("typeof STATE !== 'undefined' && !!STATE && !!STATE.jira"):
        klar = True
        break
print("panelen laddad:", klar)
js("show('import')")
time.sleep(1)

LÄS = """(() => {
  const alla = [...document.querySelectorAll('[id="impFiles"]')];
  const första = document.getElementById('impFiles');
  const knapp = document.getElementById('impParse');
  const etikett = document.getElementById('impFilesNote');
  return {antal_med_id: alla.length, forsta: första.tagName,
          etikett: etikett ? etikett.textContent.trim() : '(saknas)',
          input_varde: första.tagName === 'INPUT' ? String(första.value) : '',
          filer_i_js: (typeof IMP !== 'undefined' && IMP.files) ? IMP.files.length : -1,
          knapp_av: knapp ? knapp.disabled : null};
})()"""

print("FÖRE val:", json.dumps(js(LÄS), ensure_ascii=False))

rot = cdp("DOM.getDocument", depth=-1)["root"]["nodeId"]
nod = cdp("DOM.querySelector", nodeId=rot, selector="#impFiles")["nodeId"]
print("input-nod:", nod)
cdp("DOM.setFileInputFiles", files=[FIL], nodeId=nod)
time.sleep(1.5)
print("EFTER val:", json.dumps(js(LÄS), ensure_ascii=False))

ws.send(json.dumps({"id": 9999, "method": "Browser.close"}))
chrome.terminate()
