#!/usr/bin/env python3
"""A page in its own headless chromium, spoken to over a minimal CDP websocket client.

Standard library only. The panel is a SPA that fetches its data after load, so a
measurement has to wait for a POSITIVE signal and then ask the page itself -- hence
js() and wait_for().

    page = open_page("http://127.0.0.1:8788/#overview")
    page.wait_for("typeof STATE !== 'undefined' && !!STATE && !!STATE.jira")
    print(page.js("document.title"))

Setting a file on an <input type=file> is only possible here (DOM.setFileInputFiles):
the same path a real file picker takes, i.e. a real change event instead of a made-up one.
"""
import base64
import json
import os
import socket
import struct
import subprocess
import time
import urllib.request


class Page:
    """One page. Send a CDP command, read the matching reply, ignore the events between."""

    def __init__(self, sock, buffered, profile, port):
        self.sock, self.buf = sock, bytearray(buffered)
        self.profile, self.port, self.n = profile, port, 0
        self.chrome = None

    # ------------------------------------------------------------------ CDP
    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("connection closed")
            self.buf += chunk
        out, self.buf = bytes(self.buf[:n]), self.buf[n:]
        return out

    def _send(self, text):
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

    def _receive(self):
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
                raise RuntimeError("connection closed")

    def cdp(self, method, **params):
        self.n += 1
        self._send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            line = json.loads(self._receive())
            if line.get("id") == self.n:
                if "error" in line:
                    raise RuntimeError(method + ": " + str(line["error"])[:200])
                return line.get("result", {})

    # ----------------------------------------------------------------- page
    def js(self, expression):
        answer = self.cdp("Runtime.evaluate", expression=expression, returnByValue=True,
                          awaitPromise=True)
        return (answer.get("result") or {}).get("value")

    def wait_for(self, expression, seconds=60):
        """Wait for a POSITIVE signal; a half-loaded page measures as an app bug."""
        for _ in range(max(1, int(seconds / 1.5))):
            if self.js(expression):
                return True
            time.sleep(1.5)
        return False

    def goto(self, url):
        self.cdp("Page.enable")
        self.cdp("Page.navigate", url=url)

    def size(self, width, height):
        self.cdp("Emulation.setDeviceMetricsOverride", width=width, height=height,
                 deviceScaleFactor=1, mobile=False)

    def set_file(self, selector, path):
        """Hand a real file to a file input, the way a file picker would."""
        root = self.cdp("DOM.getDocument", depth=-1)["root"]["nodeId"]
        node = self.cdp("DOM.querySelector", nodeId=root, selector=selector)["nodeId"]
        self.cdp("DOM.setFileInputFiles", files=[path], nodeId=node)
        return node

    def close(self):
        try:
            self.cdp("Browser.close")
        except Exception:
            pass


def open_page(url="about:blank", port=9350, profile="/tmp/cdp-page", width=1500, height=940):
    """Start a chromium of our own on its own profile, and hand back the page."""
    subprocess.run(["pkill", "-f", "user-data-dir=" + profile], capture_output=True)
    chrome = subprocess.Popen(
        ["chromium", "--headless=new", "--remote-debugging-port=%d" % port,
         "--remote-allow-origins=*", "--no-sandbox", "--no-first-run", "--disable-gpu",
         "--user-data-dir=" + profile, "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws_url = ""
    for _ in range(60):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=2) as answer:
                pages = [t for t in json.load(answer) if t.get("type") == "page"]
                ws_url = pages[0]["webSocketDebuggerUrl"]
            break
        except Exception:
            time.sleep(0.5)
    if not ws_url:
        chrome.terminate()
        raise RuntimeError("chromium did not answer on the remote port")
    hostport = ws_url.split("//", 1)[1].split("/", 1)[0]
    host, port_in_url = hostport.rsplit(":", 1)
    sock = socket.create_connection((host, int(port_in_url)), timeout=120)
    key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall(("GET /devtools/page/%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n"
                  "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n"
                  "Origin: http://127.0.0.1\r\n\r\n"
                  % (ws_url.rstrip("/").rsplit("/", 1)[1], hostport, key)).encode())
    handshake = sock.recv(4096)
    if b" 101" not in handshake.split(b"\r\n")[0]:
        chrome.terminate()
        raise RuntimeError("websocket handshake failed: " + str(handshake[:120]))
    page = Page(sock, handshake.split(b"\r\n\r\n", 1)[1], profile, port)
    page.chrome = chrome
    page.size(width, height)
    if url != "about:blank":
        page.goto(url)
    return page
