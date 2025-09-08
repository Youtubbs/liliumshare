import os, base64, argparse, json, pathlib, requests
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

CONFIG_DIR = pathlib.Path.home() / ".liliumshare"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
KEYS_FILE = CONFIG_DIR / "keys.json"

# --- centralized network config loader ---
import json as _json
from pathlib import Path as _Path

def _load_netcfg():
    # 1) env override
    env_path = os.getenv("LILIUM_NETCFG")
    if env_path:
        p = _Path(env_path)
    else:
        # 2) repo-root/backend/network_config.json (search a few parents)
        here = _Path(__file__).resolve()
        candidates = [
            here.parent / "backend" / "network_config.json",
            here.parent.parent / "backend" / "network_config.json",
            here.parents[2] / "backend" / "network_config.json",
        ]
        p = next((c for c in candidates if c.exists()), None)
    data = {}
    if p and p.exists():
        try:
            data = _json.loads(p.read_text())
        except Exception:
            data = {}
    be = data.get("backend", {})
    http_base = be.get("http_base", "http://localhost:18080")
    ws_base   = be.get("ws_base",   http_base.replace("http://","ws://").replace("https://","wss://").rstrip("/") + "/ws")
    return {"http_base": http_base, "ws_base": ws_base}

_NETCFG = _load_netcfg()
_DEFAULT_HTTP_BASE = _NETCFG["http_base"]
# ------------------------------------------

def _b64(x: bytes) -> str:
    return base64.b64encode(x).decode()

def _der_pubkey_bytes(private_key) -> bytes:
    pub = private_key.public_key()
    return pub.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo
    )

def _der_privkey_bytes(private_key) -> bytes:
    return private_key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()
    )

def load_or_create():
    if KEYS_FILE.exists():
        with open(KEYS_FILE, "r") as f:
            return json.load(f)
    sk = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    data = {
        "private": _b64(_der_privkey_bytes(sk)),
        "public":  _b64(_der_pubkey_bytes(sk)),
        "nickname": None
    }
    with open(KEYS_FILE, "w") as f:
        json.dump(data, f, indent=2)
    return data

def get_keys():
    with open(KEYS_FILE, "r") as f:
        return json.load(f)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--generate", action="store_true", help="Generate a new keypair")
    ap.add_argument("--nickname", type=str, help="Set nickname")
    ap.add_argument("--register", action="store_true", help="Register public key with backend")
    ap.add_argument("--backend", default=_DEFAULT_HTTP_BASE)
    args = ap.parse_args()

    if args.generate:
        if KEYS_FILE.exists():
            print("Keys already exist at", KEYS_FILE)
        else:
            data = load_or_create()
            if args.nickname:
                data["nickname"] = args.nickname
                with open(KEYS_FILE, "w") as f:
                    json.dump(data, f, indent=2)
            print("Generated. Public key (base64 DER):\n", data["public"])
            return

    data = get_keys()
    if args.nickname:
        data["nickname"] = args.nickname
        with open(KEYS_FILE, "w") as f:
            json.dump(data, f, indent=2)

    if args.register:
        pub = data["public"]
        nickname = data.get("nickname")
        r = requests.post(f"{args.backend}/api/register", json={"pubkey": pub, "nickname": nickname})
        print("Register status:", r.status_code, r.text)
        return

    print("Public key:", data["public"])
    if data.get("nickname"):
        print("Nickname:", data["nickname"])

if __name__ == "__main__":
    main()
