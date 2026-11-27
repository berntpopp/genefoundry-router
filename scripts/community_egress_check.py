"""Federation Standard v1 §2 reachability check, run INSIDE the community container.

    docker compose ... exec -T genefoundry-community python - < scripts/community_egress_check.py

Every --blocked target (plus the host gateway and the cloud metadata address) must be
unreachable: name resolution fails, the connect times out, or the network is unreachable.
A refused connection means the host answered, so it counts as reachable. Optional --open
targets must connect (proves the check is not vacuous). Exit 0 = isolated. Stdlib only.
"""

from __future__ import annotations

import argparse
import errno
import socket
import sys

METADATA = "169.254.169.254:80"
GATEWAY_PORTS = (22, 2375, 8000)  # ssh, Docker API, a host-published router port
BLOCKED_ERRNOS = {errno.ETIMEDOUT, errno.EHOSTUNREACH, errno.ENETUNREACH}


def host_gateway(route_file: str = "/proc/net/route") -> str | None:
    """IPv4 default gateway of this network namespace (the Docker host's bridge address)."""
    try:
        with open(route_file) as handle:
            for line in handle.readlines()[1:]:
                fields = line.split()
                if len(fields) > 2 and fields[1] == "00000000":
                    return socket.inet_ntoa(int(fields[2], 16).to_bytes(4, "little"))
    except OSError:
        return None
    return None


def reachable(target: str, timeout: float) -> tuple[bool, str]:
    host, _, port = target.rpartition(":")
    try:
        with socket.create_connection((host.strip("[]"), int(port)), timeout=timeout):
            return True, "connected"
    except socket.gaierror:
        return False, "name does not resolve"
    except TimeoutError:
        return False, "timed out"
    except OSError as exc:
        if exc.errno in BLOCKED_ERRNOS:
            return False, exc.strerror or "unreachable"
        return True, exc.strerror or str(exc)  # refused or reset: something answered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--blocked", action="append", default=[], metavar="HOST:PORT")
    parser.add_argument("--open", action="append", default=[], metavar="HOST:PORT")
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args(argv)
    blocked = [*args.blocked, METADATA]
    gateway = host_gateway()
    if gateway is None:
        print("FAIL no default gateway found; cannot test the Docker host")
        return 1
    blocked += [f"{gateway}:{port}" for port in GATEWAY_PORTS]
    failed = False
    for target in blocked:
        ok, why = reachable(target, args.timeout)
        failed |= ok
        print(f"{'FAIL reachable' if ok else 'ok   blocked  '} {target} ({why})")
    for target in args.open:
        ok, why = reachable(target, args.timeout)
        failed |= not ok
        print(f"{'ok   open     ' if ok else 'FAIL no egress '} {target} ({why})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
