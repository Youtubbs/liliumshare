import argparse, subprocess, sys, json, pathlib

def main():
    ap = argparse.ArgumentParser(description="LiliumShare client (host/view)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    host = sub.add_parser("host")
    host.add_argument("--ws", default="ws://localhost:8081/ws")

    view = sub.add_parser("view")
    view.add_argument("--host", required=True, help="Host public key")
    view.add_argument("--ws", default="ws://localhost:8081/ws")

    args = ap.parse_args()

    if args.cmd == "host":
        subprocess.run([sys.executable, (pathlib.Path(__file__).parent / "rtc_host.py").as_posix(), "--ws", args.ws])
    elif args.cmd == "view":
        subprocess.run([sys.executable, (pathlib.Path(__file__).parent / "rtc_viewer.py").as_posix(), "--host", args.host, "--ws", args.ws])

if __name__ == "__main__":
    main()
