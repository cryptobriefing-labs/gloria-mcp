"""Start gloria_mcp.server with every non-loopback socket connect refused.

Usage: <venv>/bin/python -I guarded_server.py <repo>/src <denied-log-path>

Used by mcp_contract_smoke.py. Each refused address is appended to the log
file at once, so the record survives the server being killed.
"""

import socket
import sys

SRC, DENIED_LOG = sys.argv[1], sys.argv[2]
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_orig_connect = socket.socket.connect
_orig_connect_ex = socket.socket.connect_ex


def _allowed(address):
    if isinstance(address, tuple) and address:
        return str(address[0]) in _LOOPBACK
    return isinstance(address, (str, bytes))


def _record(address):
    with open(DENIED_LOG, "a") as fh:
        fh.write(repr(address) + "\n")


def _connect(self, address):
    if not _allowed(address):
        _record(address)
        raise ConnectionRefusedError(f"offline smoke refused {address!r}")
    return _orig_connect(self, address)


def _connect_ex(self, address):
    if not _allowed(address):
        _record(address)
        return 111
    return _orig_connect_ex(self, address)


socket.socket.connect = _connect
socket.socket.connect_ex = _connect_ex
sys.argv = [sys.argv[0]]
sys.path.insert(0, SRC)

from gloria_mcp.server import main  # noqa: E402

main()
