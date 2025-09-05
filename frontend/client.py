#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
import sys
from typing import Optional

# Import the actual runners
# (These modules handle the flexible identity resolution for "your" pubkey.)
from urllib.parse import urlparse, urlunparse, urlencode
from urllib.request import urlopen

from rtc_host import run_host as host_run
from rtc_viewer import run_viewer as viewer_run  # NOTE: viewer_run is synchronous

# --- centralized network config loader ---
import os, json
from pathlib import Path

def _load_netcfg():
    # Allow override via env, otherwise use repo_root/backend/network_config.json
    env_path = os.getenv("LILIUM_NETCFG")
    if env_path:
        p = Path(env_path)
    else:
        # file location → repo root → backend/network_config.json
        here = Path(__file__).resolve()
        repo_root = here.parents[1]  # repo/<frontend|scripts>/<thisfile> → repo
        p = repo_root / "backend" / "network_config.json"
    try:
        data = json.loads(p.read_text())
    except Exception:
        data = {}
    # sane defaults
    be = data.get("backend", {})
    http_base = be.get("http_base", "http://localhost:18080")
    ws_base = be.get("ws_base", http_base.replace("http://", "ws://").replace("https://","wss://").rstrip("/") + "/ws")
    return {"http_base": http_base, "ws_base": ws_base}

NETCFG = _load_netcfg()
DEFAULT_HTTP_BASE = NETCFG["http_base"]
DEFAULT_WS_BASE   = NETCFG["ws_base"]
# ------------------------------------------

def http_base_from_ws(ws_url: str) -> str:
    # honor explicit ws_url if passed; otherwise use DEFAULTs from config
    if ws_url:
        from urllib.parse import urlparse, urlunparse
        u = urlparse(ws_url)
        scheme = "https" if u.scheme == "wss" else "http"
        return urlunparse((scheme, u.netloc, "", "", "", ""))
    return DEFAULT_HTTP_BASE

def fetch_pubkey_by_nick(ws_url: str, nickname: str) -> str:
    base = http_base_from_ws(ws_url)
    url = f"{base}/api/users/by-nickname?{urlencode({'nickname': nickname})}"
    with urlopen(url, timeout=5) as r:
        data = json.loads(r.read().decode())
        return data["pubkey"]

def main():
    ap = argparse.ArgumentParser(description="LiliumShare client")
    sub = ap.add_subparsers(dest="cmd", required=True)

    default_ws = DEFAULT_WS_BASE

    aph = sub.add_parser("host", help="Run as host (share your screen)")
    aph.add_argument("--ws", default=default_ws, help="Signaling WS URL")
    aph.add_argument("--pubkey", help="Override YOUR pubkey (base64)")
    aph.add_argument("--nick", help="Set LILIUM_NICK for YOUR identity lookup")

    apv = sub.add_parser("view", help="Run as viewer (watch host)")
    apv.add_argument("--ws", default=default_ws, help="Signaling WS URL")
    apv.add_argument("--host", help="HOST pubkey (base64)")
    apv.add_argument("--host-nick", help="Resolve HOST by nickname via backend")
    apv.add_argument("--pubkey", help="Override YOUR pubkey (base64)")
    apv.add_argument("--nick", help="Set LILIUM_NICK for YOUR identity lookup")

    args = ap.parse_args()

    if getattr(args, "nick", None):
        os.environ["LILIUM_NICK"] = args.nick

    if args.cmd == "host":
        asyncio.run(host_run(args.ws, args.pubkey))
        return

    if args.cmd == "view":
        host_pub = args.host
        if not host_pub and args.host_nick:
            try:
                host_pub = fetch_pubkey_by_nick(args.ws, args.host_nick)
            except Exception as e:
                print(f"Failed to resolve host nickname '{args.host_nick}': {e}", file=sys.stderr)
                sys.exit(2)
        if not host_pub:
            print("You must provide --host <pubkey> or --host-nick <nickname>.", file=sys.stderr)
            sys.exit(2)

        # IMPORTANT: viewer_run is synchronous (GUI on main thread)
        viewer_run(host_pub, args.ws, args.pubkey)
        return

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        # print a concise error and exit nonzero
        sys.stderr.write(f"{e}\n")
        sys.exit(1)