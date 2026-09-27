#!/usr/bin/env python3
"""Who owns the Solo server's port, and which processes are our servers.

Used by start_solo.sh / stop_solo.sh. Reads /proc directly (QLDS is Linux-only)
so it works without `ss` and reports owner PIDs, which `ss -lun` does not.

  solo_ports.py owners PORT QLDS_BIN   -> "PID solo|foreign PROTO NAME" per owner
  solo_ports.py servers PORT QLDS_BIN  -> PIDs of our qzeroded for this port
  solo_ports.py owns PID PORT          -> exit 0 if PID has UDP PORT bound
  solo_ports.py is-solo PID QLDS_BIN   -> exit 0 if PID is our qzeroded

A process is "ours" when it runs QLDS_BIN (by /proc/PID/exe or argv[0]).
"Our server for this port" also covers a server that was *told* to use PORT
(+set net_port PORT) but fell back to PORT+1 because PORT was taken.
Nothing here ever reports a foreign process as ours, so callers can only kill
Solo servers.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

TCP_LISTEN = "0A"


def _socket_inodes(port: int) -> dict[str, str]:
    """inode -> protocol for sockets bound to PORT (UDP any state, TCP listening)."""
    found: dict[str, str] = {}
    for proto, table in (("udp", "udp"), ("udp", "udp6"), ("tcp", "tcp"), ("tcp", "tcp6")):
        try:
            lines = Path("/proc/net", table).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) < 10:
                continue
            try:
                local_port = int(parts[1].rsplit(":", 1)[1], 16)
            except (IndexError, ValueError):
                continue
            if local_port != port:
                continue
            if proto == "tcp" and parts[3] != TCP_LISTEN:
                continue
            inode = parts[9]
            if inode != "0":
                found[inode] = proto
    return found


def _pids():
    for entry in os.scandir("/proc"):
        if entry.name.isdigit():
            yield int(entry.name)


def _socket_owners(port: int) -> dict[int, set[str]]:
    inodes = _socket_inodes(port)
    owners: dict[int, set[str]] = {}
    if not inodes:
        return owners
    for pid in _pids():
        try:
            fds = os.scandir(f"/proc/{pid}/fd")
        except OSError:
            continue
        with fds:
            for fd in fds:
                try:
                    target = os.readlink(fd.path)
                except OSError:
                    continue
                if target.startswith("socket:[") and target[8:-1] in inodes:
                    owners.setdefault(pid, set()).add(inodes[target[8:-1]])
    return owners


def _cmdline(pid: int) -> list[str]:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return []
    return [a.decode(errors="replace") for a in raw.split(b"\0") if a]


def _name(pid: int) -> str:
    args = _cmdline(pid)
    if args:
        return os.path.basename(args[0])
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        return "?"


def is_solo(pid: int, qlds_bin: str) -> bool:
    want = os.path.realpath(qlds_bin)
    try:
        if os.path.realpath(os.readlink(f"/proc/{pid}/exe")) == want:
            return True
    except OSError:
        pass
    args = _cmdline(pid)
    return bool(args) and os.path.realpath(args[0]) == want


def _configured_port(pid: int) -> int | None:
    args = _cmdline(pid)
    for i in range(len(args) - 2):
        if args[i] == "+set" and args[i + 1] == "net_port":
            try:
                return int(args[i + 2])
            except ValueError:
                return None
    return None


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    cmd = argv[0]
    if cmd == "owners":
        port, qlds_bin = int(argv[1]), argv[2]
        for pid, protos in sorted(_socket_owners(port).items()):
            tag = "solo" if is_solo(pid, qlds_bin) else "foreign"
            print(pid, tag, ",".join(sorted(protos)), _name(pid))
        return 0
    if cmd == "servers":
        port, qlds_bin = int(argv[1]), argv[2]
        pids = {pid for pid in _socket_owners(port) if is_solo(pid, qlds_bin)}
        me = os.getpid()
        for pid in _pids():
            if pid != me and pid not in pids and is_solo(pid, qlds_bin) and _configured_port(pid) == port:
                pids.add(pid)
        for pid in sorted(pids):
            print(pid)
        return 0
    if cmd == "owns":
        pid, port = int(argv[1]), int(argv[2])
        return 0 if "udp" in _socket_owners(port).get(pid, set()) else 1
    if cmd == "is-solo":
        return 0 if is_solo(int(argv[1]), argv[2]) else 1
    print(f"unknown command {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
