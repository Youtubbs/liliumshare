#!/usr/bin/env python3
import argparse, base64, json, os, pathlib, sys
import requests
from nacl import signing

# Example usage: 
# python3 scripts/bootstrap_local_dual_user.py \
#  --base http://localhost:8081 \
#  --ahome "$HOME/liliumshare/_userA_home" \
#  --bhome "$HOME/liliumshare/_userB_home"

def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()

def ensure_keys(home_dir: pathlib.Path, nickname: str) -> dict:
    cfg = home_dir / ".liliumshare"
    cfg.mkdir(parents=True, exist_ok=True)
    kf = cfg / "keys.json"
    if kf.exists():
        return json.loads(kf.read_text())
    sk = signing.SigningKey.generate()
    vk = sk.verify_key
    data = {"private": b64(sk.encode()), "public": b64(vk.encode()), "nickname": nickname}
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
    skA = signing.SigningKey.generate(); vkA = skA.verify_key
    skB = signing.SigningKey.generate(); vkB = skB.verify_key
    A_PUB = b64(vkA.encode()); B_PUB = b64(vkB.encode())

    if args.write_homes:  # <-- FIXED (underscore)
        A_HOME = pathlib.Path(args.ahome).resolve()
        B_HOME = pathlib.Path(args.bhome).resolve()
        a = {"private": b64(skA.encode()), "public": A_PUB, "nickname": args.anick}
        b = {"private": b64(skB.encode()), "public": B_PUB, "nickname": args.bnick}
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

    # Verify
    ver = get_params(base, "/api/friends/permissions", {"host": A_PUB, "friend": B_PUB})

    print("\n=== LiliumShare Local Bootstrap OK ===")
    print("Backend:", base)
    print("WS:", ws)
    print("Host (A) pubkey:", A_PUB)
    print("Viewer (B) pubkey:", B_PUB)
    print("Permissions:", json.dumps(ver, indent=2))

    print("\nRun in two terminals (no HOME needed):")
    print(f'  python frontend/client.py host  --ws {ws} --pubkey "{A_PUB}"')
    print(f'  python frontend/client.py view  --ws {ws} --host "{A_PUB}" --pubkey "{B_PUB}"')

    if args.write_homes:  # <-- FIXED (underscore)
        print("\nOr, using HOME-based keys (created now):")
        print(f'  HOME="{A_HOME}" python frontend/client.py host  --ws {ws}')
        print(f'  HOME="{B_HOME}" python frontend/client.py view  --ws {ws} --host "{A_PUB}"')

if __name__ == "__main__":
    try:
        import requests  # noqa
        from nacl import signing  # noqa
    except Exception:
        print("You need: pip install requests PyNaCl", file=sys.stderr); sys.exit(2)
    main()

