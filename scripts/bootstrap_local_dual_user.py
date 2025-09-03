#!/usr/bin/env python3
import argparse, base64, json, os, pathlib, sys
import requests
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

# Example usage: 
# python3 scripts/bootstrap_local_dual_user.py \
#  --base http://localhost:8081 \
#  --ahome "$HOME/liliumshare/_userA_home" \
#  --bhome "$HOME/liliumshare/_userB_home"

def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()

def gen_rsa():
    sk = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_der = sk.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    prv_der = sk.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return b64(prv_der), b64(pub_der)

def ensure_keys(home_dir: pathlib.Path, nickname: str) -> dict:
    cfg = home_dir / ".liliumshare"
    cfg.mkdir(parents=True, exist_ok=True)
    kf = cfg / "keys.json"
    if kf.exists():
        return json.loads(kf.read_text())
    prv, pub = gen_rsa()
    data = {"private": prv, "public": pub, "nickname": nickname}
    kf.write_text(json.dumps(data, indent=2))
    return data

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
    ap = argparse.ArgumentParser(description="Create two users, register, friend, set permissions, and print run commands.")
    ap.add_argument("--base", default="http://localhost:8081", help="Backend base URL (http)")
    ap.add_argument("--write-homes", action="store_true", help="Also write ~/.liliumshare/keys.json under the two HOME dirs")
    ap.add_argument("--ahome", default=str(pathlib.Path.cwd() / "_userA_home"), help="Host A HOME dir (only if --write-homes)")
    ap.add_argument("--bhome", default=str(pathlib.Path.cwd() / "_userB_home"), help="Viewer B HOME dir (only if --write-homes)")
    ap.add_argument("--anick", default="HostA", help="Nickname for A")
    ap.add_argument("--bnick", default="ViewerB", help="Nickname for B")
    args = ap.parse_args()

    base = args.base.rstrip("/")
    ws = base.replace("http://","ws://").replace("https://","wss://") + "/ws"

    # Generate in-memory keys; optionally write to homes
    prvA, A_PUB = gen_rsa()
    prvB, B_PUB = gen_rsa()

    if args.write_homes:  
        A_HOME = pathlib.Path(args.ahome).resolve()
        B_HOME = pathlib.Path(args.bhome).resolve()
        a = {"private": prvA, "public": A_PUB, "nickname": args.anick}
        b = {"private": prvB, "public": B_PUB, "nickname": args.bnick}
        (A_HOME / ".liliumshare").mkdir(parents=True, exist_ok=True)
        (B_HOME / ".liliumshare").mkdir(parents=True, exist_ok=True)
        (A_HOME / ".liliumshare" / "keys.json").write_text(json.dumps(a, indent=2))
        (B_HOME / ".liliumshare" / "keys.json").write_text(json.dumps(b, indent=2))

    # Register
    post(base, "/api/register", {"pubkey": A_PUB, "nickname": args.anick})
    post(base, "/api/register", {"pubkey": B_PUB, "nickname": args.bnick})

    # Upsert friendship + permissions
    perms = {"autoJoin": True, "keyboard": True, "mouse": True, "controller": False, "immersion": False}
    post(base, "/api/friends/upsert", {"host": A_PUB, "friend": B_PUB, "permissions": perms})

    # Generate per-friendship connection keys
    post(base, "/api/friends/connkey/generate", {"host": A_PUB, "friend": B_PUB})

    # Verify
    ver = get_params(base, "/api/friends/permissions", {"host": A_PUB, "friend": B_PUB})

    print("\n=== LiliumShare Local Bootstrap OK (RSA) ===")
    print("Backend:", base)
    print("WS:", ws)
    print("Host (A) pubkey:", A_PUB)
    print("Viewer (B) pubkey:", B_PUB)
    print("Permissions+ConnKeys:", json.dumps(ver, indent=2))

    print("\nRun in two terminals (no HOME needed):")
    print(f'  python frontend/client.py host  --ws {ws} --pubkey "{A_PUB}"')
    print(f'  python frontend/client.py view  --ws {ws} --host "{A_PUB}" --pubkey "{B_PUB}"')

    if args.write_homes:  
        print("\nOr, using HOME-based keys (created now):")
        print(f'  HOME="{A_HOME}" python frontend/client.py host  --ws {ws}')
        print(f'  HOME="{B_HOME}" python frontend/client.py view  --ws {ws} --host "{A_PUB}"')

if __name__ == "__main__":
    try:
        import requests  # noqa
        from cryptography.hazmat.primitives.asymmetric import rsa  # noqa
    except Exception:
        print("You need: pip install requests cryptography", file=sys.stderr); sys.exit(2)
    main()
