#!/usr/bin/env python3
import argparse, base64, json, pathlib, sys
import requests
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

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

def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()

def gen_rsa_pair():
    sk = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_der = sk.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    prv_der = sk.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return b64(prv_der), b64(pub_der)

def write_keys(home_dir: pathlib.Path, nickname: str, prv_b64: str, pub_b64: str):
    cfg = home_dir / ".liliumshare"
    cfg.mkdir(parents=True, exist_ok=True)
    data = {"private": prv_b64, "public": pub_b64, "nickname": nickname}
    (cfg / "keys.json").write_text(json.dumps(data, indent=2))

def post(base: str, path: str, payload: dict) -> dict:
    r = requests.post(f"{base}{path}", json=payload, timeout=10)
    try: body = r.json()
    except Exception: body = r.text
    if not (200 <= r.status_code < 300):
        print(f"ERROR {path} {r.status_code}: {body}", file=sys.stderr); sys.exit(1)
    return body

def get_params(base: str, path: str, params: dict) -> dict:
    r = requests.get(f"{base}{path}", params=params, timeout=10)
    try: body = r.json()
    except Exception: body = r.text
    if not (200 <= r.status_code < 300):
        print(f"ERROR {path} {r.status_code}: {body}", file=sys.stderr); sys.exit(1)
    return body

def main():
    ap = argparse.ArgumentParser(description="Bootstrap 3 local users (A,B friends; C neutral, plus C->A pending).")
    ap.add_argument("--base", default=DEFAULT_HTTP_BASE, help="Backend base URL (http)")
    ap.add_argument("--anick", default="HostA")
    ap.add_argument("--bnick", default="ViewerB")
    ap.add_argument("--cnick", default="UserC")
    ap.add_argument("--ahome", default=str(pathlib.Path.cwd() / "_userA_home"))
    ap.add_argument("--bhome", default=str(pathlib.Path.cwd() / "_userB_home"))
    ap.add_argument("--chome", default=str(pathlib.Path.cwd() / "_userC_home"))
    args = ap.parse_args()

    base = args.base.rstrip("/")
    ws = DEFAULT_WS_BASE if args.base == DEFAULT_HTTP_BASE else args.base.replace("http://","ws://").replace("https://","wss://").rstrip("/") + "/ws"

    prvA, A_PUB = gen_rsa_pair()
    prvB, B_PUB = gen_rsa_pair()
    prvC, C_PUB = gen_rsa_pair()

    write_keys(pathlib.Path(args.ahome), args.anick, prvA, A_PUB)
    write_keys(pathlib.Path(args.bhome), args.bnick, prvB, B_PUB)
    write_keys(pathlib.Path(args.chome), args.cnick, prvC, C_PUB)

    # Register all three
    post(base, "/api/register", {"pubkey": A_PUB, "nickname": args.anick})
    post(base, "/api/register", {"pubkey": B_PUB, "nickname": args.bnick})
    post(base, "/api/register", {"pubkey": C_PUB, "nickname": args.cnick})

    # Accept A<->B both directions with default perms
    perms = {"autoJoin": True, "keyboard": True, "mouse": True, "controller": False, "immersion": False}
    post(base, "/api/friends/upsert", {"host": A_PUB, "friend": B_PUB, "permissions": perms})

    # Create a pending C -> A so A sees an incoming request
    post(base, "/api/friends/request", {"me": C_PUB, "friend": A_PUB})

    # Print summaries
    print("\n=== Bootstrap complete ===")
    print("Backend:", base)
    print("WS:", ws)
    print("A pub:", A_PUB)
    print("B pub:", B_PUB)
    print("C pub:", C_PUB)

    la = get_params(base, "/api/friends/list", {"me": A_PUB})
    lb = get_params(base, "/api/friends/list", {"me": B_PUB})
    lc = get_params(base, "/api/friends/list", {"me": C_PUB})
    print("\nA list:", json.dumps(la, indent=2))
    print("\nB list:", json.dumps(lb, indent=2))
    print("\nC list:", json.dumps(lc, indent=2))

    print("\nRun GUIs with per-user homes:")
    print(f'  HOME="{pathlib.Path(args.ahome)}" python3 frontend/gui.py')
    print(f'  HOME="{pathlib.Path(args.bhome)}" python3 frontend/gui.py')
    print(f'  HOME="{pathlib.Path(args.chome)}" python3 frontend/gui.py')

if __name__ == "__main__":
    try:
        import requests  # noqa
        from cryptography.hazmat.primitives.asymmetric import rsa  # noqa
    except Exception:
        print("You need: pip install requests cryptography", file=sys.stderr); sys.exit(2)
    main()
