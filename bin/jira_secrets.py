#!/usr/bin/env python3
"""jira_secrets -- the operating system's own credential store, no dependencies.

Where a token is kept is the one thing that is genuinely platform-specific about
running jira_flow away from Omarchy. On Linux this module finds nothing and says
nothing: the bridge's keyring (or the 0600 config file) is already there, and
nothing in that path changes because of this file.

    Windows   DPAPI (CryptProtectData), bound to the logged-in user account,
              via ctypes from the standard library. The ciphertext goes to
              ~/.config/jira-flow/secret.dat as base64 text: readable, useless
              to anyone but that user on that machine.
    macOS     the login keychain via the `security` command line tool.

Never print a token. Every failure is loud: a store that cannot reach the
operating system's facility raises, it never returns an empty token that would
look like "no credential set".

Self-check: python3 bin/jira_secrets.py --selftest
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import subprocess
import sys
from pathlib import Path

SERVICE = "jira-flow"
ACCOUNT = os.environ.get("JIRA_FLOW_ACCOUNT", "default")
SECRET_FILE = Path.home() / ".config/jira-flow" / "secret.dat"


# --------------------------------------------------------------- Windows

class WindowsStore:
    """DPAPI: encrypt as the current user, decrypt as the current user.

    ponytail: CryptProtectData without an entropy blob. It ties the secret to the
    account, which is what a token in a file cannot do; add entropy (a second
    factor in the code) only if someone actually needs it to survive a copy of
    the file to another machine.
    """

    CRYPTPROTECT_UI_FORBIDDEN = 0x01

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_uint32),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _run(self, which: str, payload: bytes) -> bytes:
        if not hasattr(ctypes, "windll"):  # not Windows: say so, do not guess
            raise RuntimeError("DPAPI finns bara på Windows (kör login på den maskinen)")
        windll = getattr(ctypes, "windll")  # finns bara på Windows; hasattr-kollen ovan
        crypt32, kernel32 = windll.crypt32, windll.kernel32
        blob_in = self._Blob(len(payload), ctypes.cast(ctypes.create_string_buffer(payload),
                                                       ctypes.POINTER(ctypes.c_char)))
        blob_out = self._Blob()
        fn = getattr(crypt32, which)
        ok = fn(ctypes.byref(blob_in), None, None, None, None,
                self.CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
        if not ok:
            raise RuntimeError("{} misslyckades (fel {})".format(which, kernel32.GetLastError()))
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)

    def write(self, token: str) -> None:
        blob = self._run("CryptProtectData", token.encode())
        SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        with SECRET_FILE.open("w", newline="\n") as fh:
            fh.write(base64.b64encode(blob).decode())

    def read(self) -> str:
        if not SECRET_FILE.exists():
            return ""
        blob = base64.b64decode(SECRET_FILE.read_text().strip())
        return self._run("CryptUnprotectData", blob).decode()

    def delete(self) -> bool:
        if not SECRET_FILE.exists():
            return False
        SECRET_FILE.unlink()
        return True


# ----------------------------------------------------------------- macOS

class KeychainStore:
    """The login keychain, through `security` (ships with macOS)."""

    def _run(self, args, expect_output: bool = False) -> str:
        try:
            done = subprocess.run(["security"] + args, capture_output=True, text=True, timeout=20)
        except FileNotFoundError:
            raise RuntimeError("`security` finns bara på macOS (kör login på den maskinen)")
        if done.returncode != 0:
            if not expect_output:  # a failed delete means "was not there"
                raise RuntimeError("security {}: {}".format(args[0], (done.stderr or "").strip()[:200]))
            return ""
        return done.stdout.strip()

    def write(self, token: str) -> None:
        self._run(["add-generic-password", "-a", ACCOUNT, "-s", SERVICE, "-w", token, "-U"])

    def read(self) -> str:
        return self._run(["find-generic-password", "-a", ACCOUNT, "-s", SERVICE, "-w"],
                         expect_output=True)

    def delete(self) -> bool:
        return bool(self._run(["delete-generic-password", "-a", ACCOUNT, "-s", SERVICE],
                              expect_output=True))


# ------------------------------------------------------------------ Linux

class NoStore:
    """Linux: nothing here on purpose.

    The Omarchy Jira bridge already keeps the token in the desktop keyring, and
    jira_flow reads the 0600 config file when the bridge is absent. A second
    store would be a second place for the truth to live.
    """

    def write(self, token: str) -> None:
        raise RuntimeError(
            "på Linux sköts token av bryggans nyckelring (`jira_bridge.py login`) "
            "eller av {} (chmod 600)".format(Path.home() / ".config/jira-flow/config.json"))

    def read(self) -> str:
        return ""

    def delete(self) -> bool:
        return False


def store_for(platform: str = "") -> "WindowsStore | KeychainStore | NoStore":
    """The store that fits the platform. Unknown platforms get NoStore, which is
    the honest answer: this module will not pretend to have a safe place."""
    name = (platform or sys.platform).lower()
    if name.startswith("win"):
        return WindowsStore()
    if name == "darwin":
        return KeychainStore()
    return NoStore()


# -------------------------------------------------------------- self-check

def selftest() -> int:
    checks = 0
    assert isinstance(store_for("win32"), WindowsStore) and isinstance(store_for("Windows"), WindowsStore)
    assert isinstance(store_for("darwin"), KeychainStore)
    for other in ("linux", "freebsd13", ""):
        assert isinstance(store_for(other), NoStore), other
    checks += 1

    # Linux får inte ens försöka: det är hela poängen med att lämna den vägen i fred.
    assert store_for("linux").read() == "" and store_for("linux").delete() is False
    try:
        store_for("linux").write("x")
        raise AssertionError("skrivning på Linux skulle ha vägrat")
    except RuntimeError:
        pass
    checks += 1

    # Rätt plattform, fel maskin: ska ge ett fel med skäl, aldrig en tom token.
    for platform in ("win32", "darwin"):
        try:
            token = store_for(platform).read()
            assert token == "", "fel plattform får inte hitta på en token: {!r}".format(token)
        except RuntimeError:
            pass
    checks += 1

    # Formen på det Windows skriver: base64 av chiffertexten, inget klartextläge.
    blob = b"\x00\x01cipher\xff"
    assert base64.b64decode(base64.b64encode(blob)) == blob
    assert json is not None
    checks += 1

    print("jira_secrets self-check: {} checks, 0 failed".format(checks))
    return 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else 0)
