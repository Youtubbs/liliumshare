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

def load_or_create(home_dir: pathlib.Path, nickname: str) -> str:
    cfg = home_dir / ".liliumshare"
    cfg.mkdir(parents=True, exist_ok=True)
    kf = cfg / "keys.json"
    if kf.exists():
        data = json.loads(kf.read_text())
        if not data.get("nickname"):
            data["nickname"] = nickname
            kf.write_text(json.dumps(data, indent=2))
        return data["public"]
    sk = signing.SigningKey.generate()
    vk = sk.verify_key
    data = {"private": b64(sk.encode()), "public": b64(vk.encode()), "nickname": nickname}
    kf.write_text(json.dumps(data, indent=2))
    return data["public"]

def post(base: str, path: str, payload: dict) -> dict:
    r = requests.post(f"{base}{path}", json=payload, timeout=10)
    try: body = r.json()
    except Exception: body = r.text
    if not (200 <= r.status_code < 300):
        print(f"ERROR {path} {r.status_code}: {body}", file=sys.stderr); sys.exit(1)
    return body

def get_params(base: str, path: str, params: dict) -> dict:
    r = requests.get(f"{base}{path}", params=params, timeout=10)  # <-- encodes + and /
    try: body = r.json()
    except Exception: body = r.text
    if not (200 <= r.status_code < 300):
        print(f"ERROR {path} {r.status_code}: {body}", file=sys.stderr); sys.exit(1)
    return body

def main():
    ap = argparse.ArgumentParser(description="Create or reuse two local users and wire friendship/permissions.")
    ap.add_argument("--base", default="http://localhost:8081", help="Backend base URL")
    ap.add_argument("--ahome", default=str(pathlib.Path.cwd() / "_userA_home"), help="Host A HOME dir")
    ap.add_argument("--bhome", default=str(pathlib.Path.cwd() / "_userB_home"), help="Viewer B HOME dir")
    ap.add_argument("--force-new-keys", action="store_true", help="Overwrite keys.json in both homes")
    args = ap.parse_args()

    base = args.base.rstrip("/")
    A_HOME = pathlib.Path(args.ahome).resolve()
    B_HOME = pathlib.Path(args.bhome).resolve()

    if args.force_new_keys:
        for h in (A_HOME, B_HOME):
            kf = h / ".liliumshare" / "keys.json"
            try: kf.unlink()
            except FileNotFoundError: pass

    print("Ensuring keys exist...")
    A_PUB = load_or_create(A_HOME, "HostA")
    B_PUB = load_or_create(B_HOME, "ViewerB")

    print("\nRegistering users on", base)
    post(base, "/api/register", {"pubkey": A_PUB, "nickname": "HostA"})
    post(base, "/api/register", {"pubkey": B_PUB, "nickname": "ViewerB"})

    print("\nUpserting friendship + permissions")
    perms = {"autoJoin": True, "keyboard": True, "mouse": True, "controller": False, "immersion": False}
    post(base, "/api/friends/upsert", {"host": A_PUB, "friend": B_PUB, "permissions": perms})

    ver = get_params(base, "/api/friends/permissions", {"host": A_PUB, "friend": B_PUB})
    print("\n=== OK (same keys reused every run) ===")
    print("A_HOME:", A_HOME); print("B_HOME:", B_HOME)
    print("A_PUB:", A_PUB);    print("B_PUB:", B_PUB)
    print("Verify:", ver)
    ws = base.replace("http://","ws://").replace("https://","wss://") + "/ws"
    print("\nRun in two terminals:")
    print(f'  HOME="{A_HOME}" python frontend/client.py host --ws {ws}')
    print(f'  HOME="{B_HOME}" python frontend/client.py view --host "{A_PUB}" --ws {ws}')

if __name__ == "__main__":
    try:
        import requests  # noqa
        from nacl import signing  # noqa
    except Exception:
        print("You need: pip install requests PyNaCl", file=sys.stderr); sys.exit(2)
    main()
