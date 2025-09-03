import os, sys, json, subprocess, pathlib, tkinter as tk
from tkinter import ttk, messagebox, filedialog
import requests

DEFAULT_HOME = str(pathlib.Path.home())
DEFAULT_KEYS_FILE = pathlib.Path(DEFAULT_HOME) / ".liliumshare" / "keys.json"

def ws_from_http(base: str) -> str:
    base = base.rstrip("/")
    return base.replace("http://","ws://").replace("https://","wss://") + "/ws"

def read_keys_from(home_dir: str) -> dict:
    p = pathlib.Path(home_dir) / ".liliumshare" / "keys.json"
    if not p.exists():
        messagebox.showerror("Keys", f"Keys not found at {p}\nRun: python frontend/keys.py --generate")
        raise RuntimeError("no keys")
    return json.loads(p.read_text())

DEFAULT_PERMS = {"keyboard": True, "mouse": True, "controller": False, "immersion": False, "autoJoin": True}

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LiliumShare • Quick Connect")
        self.geometry("680x520")
        self.minsize(560, 460)

        style = ttk.Style(self)
        style.configure("TButton", padding=(10,6))
        style.configure("Header.TLabel", font=("TkDefaultFont", 12, "bold"))
        style.configure("Hint.TLabel", foreground="#666")

        self.base = tk.StringVar(value="http://localhost:8081")
        self.ws = tk.StringVar(value=ws_from_http(self.base.get()))
        self.keys_home = tk.StringVar(value=DEFAULT_HOME)
        self.nick = tk.StringVar(value="")
        self.my_pub = tk.StringVar(value="")
        self.friend_nick = tk.StringVar(value="")
        self.friend_pub = tk.StringVar(value="")
        self.role = tk.StringVar(value="viewer")  # 'host' or 'viewer'

        self._build()

    def _build(self):
        root = ttk.Frame(self, padding=12); root.pack(fill="both", expand=True)

        # Backend
        ttk.Label(root, text="Backend", style="Header.TLabel").grid(column=0, row=0, sticky="w")
        bfr = ttk.Frame(root); bfr.grid(column=0, row=1, sticky="we", pady=(4,10))
        ttk.Label(bfr, text="Base URL (http)").grid(column=0, row=0, sticky="w")
        ttk.Entry(bfr, textvariable=self.base, width=34).grid(column=1, row=0, sticky="we", padx=(6,6))
        ttk.Button(bfr, text="Apply", command=self._apply_base).grid(column=2, row=0, sticky="e")
        ttk.Label(bfr, textvariable=self.ws, style="Hint.TLabel").grid(column=1, row=1, columnspan=2, sticky="w", pady=(3,0))
        bfr.columnconfigure(1, weight=1)

        ttk.Separator(root).grid(column=0, row=2, sticky="we", pady=6)

        # Keys home
        ttk.Label(root, text="Keys Home Dir", style="Header.TLabel").grid(column=0, row=3, sticky="w")
        kh = ttk.Frame(root); kh.grid(column=0, row=4, sticky="we", pady=(4,10))
        ttk.Entry(kh, textvariable=self.keys_home).grid(column=0, row=0, sticky="we")
        ttk.Button(kh, text="Browse…", command=self._pick_keys_home).grid(column=1, row=0, padx=(6,0))
        ttk.Button(kh, text="Load My Keys", command=self._load_keys).grid(column=2, row=0, padx=(6,0))
        kh.columnconfigure(0, weight=1)

        # Identity
        ttk.Label(root, text="Your Identity", style="Header.TLabel").grid(column=0, row=5, sticky="w")
        me = ttk.Frame(root); me.grid(column=0, row=6, sticky="we", pady=(4,10))
        ttk.Entry(me, textvariable=self.my_pub).grid(column=0, row=0, sticky="we")
        ttk.Label(me, text="(public key; read-only)", style="Hint.TLabel").grid(column=1, row=0, sticky="w", padx=(8,0))
        ttk.Label(me, text="Nickname (optional)").grid(column=0, row=1, sticky="w", pady=(6,0))
        ttk.Entry(me, textvariable=self.nick, width=24).grid(column=1, row=1, sticky="w", padx=(6,6), pady=(6,0))
        ttk.Button(me, text="Register / Update", command=self._register_user).grid(column=2, row=1, sticky="w", pady=(6,0))
        me.columnconfigure(0, weight=1)

        ttk.Separator(root).grid(column=0, row=7, sticky="we", pady=6)

        # Friend + role
        ttk.Label(root, text="Who do you want to connect with?", style="Header.TLabel").grid(column=0, row=8, sticky="w")
        fr = ttk.Frame(root); fr.grid(column=0, row=9, sticky="we", pady=(4,10))
        ttk.Radiobutton(fr, text="I want to VIEW their screen", variable=self.role, value="viewer").grid(column=0, row=0, sticky="w")
        ttk.Radiobutton(fr, text="I want to SHARE my screen", variable=self.role, value="host").grid(column=1, row=0, sticky="w")
        ttk.Label(fr, text="Friend nickname (optional)").grid(column=0, row=1, sticky="w", pady=(8,0))
        ttk.Entry(fr, textvariable=self.friend_nick, width=22).grid(column=1, row=1, sticky="w", padx=(6,6), pady=(8,0))
        ttk.Button(fr, text="Resolve →", command=self._resolve_friend).grid(column=2, row=1, sticky="w", pady=(8,0))
        ttk.Label(fr, text="Friend public key").grid(column=0, row=2, sticky="w", pady=(6,0))
        ttk.Entry(fr, textvariable=self.friend_pub).grid(column=1, row=2, columnspan=2, sticky="we", padx=(6,0), pady=(6,0))
        fr.columnconfigure(1, weight=1)

        # Launch
        actions = ttk.Frame(root); actions.grid(column=0, row=10, sticky="we")
        ttk.Button(actions, text="Connect", command=self._quick_connect, width=18).grid(column=0, row=0, padx=(0,8))
        ttk.Button(actions, text="Quit", command=self.destroy, width=10).grid(column=1, row=0)

        ttk.Label(root, text="“Connect”: Register (if nickname set) → ensure friendship & permissions → generate connection keys → launch host/viewer.",
                  style="Hint.TLabel", wraplength=620, justify="left").grid(column=0, row=11, sticky="we", pady=(8,0))

        root.columnconfigure(0, weight=1)

    def _apply_base(self):
        self.ws.set(ws_from_http(self.base.get().strip()))

    def _pick_keys_home(self):
        d = filedialog.askdirectory(initialdir=self.keys_home.get() or DEFAULT_HOME, title="Select keys HOME directory")
        if d:
            self.keys_home.set(d)

    def _load_keys(self):
        try:
            k = read_keys_from(self.keys_home.get())
            self.my_pub.set(k["public"])
            if k.get("nickname"): self.nick.set(k["nickname"])
        except Exception:
            pass

    def _register_user(self):
        try:
            k = read_keys_from(self.keys_home.get())
        except Exception:
            return
        try:
            r = requests.post(self.base.get().rstrip("/") + "/api/register",
                              json={"pubkey": k["public"], "nickname": self.nick.get().strip() or None},
                              timeout=8)
            messagebox.showinfo("Register", f"{r.status_code}: {r.text}")
        except Exception as e:
            messagebox.showerror("Register", str(e))

    def _resolve_friend(self):
        nick = self.friend_nick.get().strip()
        if not nick: return
        try:
            r = requests.get(self.base.get().rstrip("/") + "/api/users/by-nickname",
                             params={"nickname": nick}, timeout=8)
            r.raise_for_status()
            self.friend_pub.set(r.json()["pubkey"])
        except Exception as e:
            messagebox.showerror("Resolve nickname", str(e))

    def _quick_connect(self):
        # 1) load my keys
        try:
            me = read_keys_from(self.keys_home.get())
        except Exception:
            return

        # 2) optional register
        try:
            if self.nick.get().strip():
                requests.post(self.base.get().rstrip("/") + "/api/register",
                              json={"pubkey": me["public"], "nickname": self.nick.get().strip()}, timeout=8)
        except Exception as e:
            messagebox.showerror("Register", str(e)); return

        # 3) resolve friend (if needed)
        if not self.friend_pub.get().strip() and self.friend_nick.get().strip():
            try:
                r = requests.get(self.base.get().rstrip("/") + "/api/users/by-nickname",
                                 params={"nickname": self.friend_nick.get().strip()}, timeout=8)
                r.raise_for_status()
                self.friend_pub.set(r.json()["pubkey"])
            except Exception as e:
                messagebox.showerror("Resolve nickname", str(e)); return

        host_pub = self.friend_pub.get().strip()
        if not host_pub:
            messagebox.showerror("Connect", "Enter friend nickname or public key."); return

        # 4) friendship & perms
        try:
            requests.post(self.base.get().rstrip("/") + "/api/friends/upsert",
                          json={"host": host_pub, "friend": me["public"], "permissions": DEFAULT_PERMS},
                          timeout=8)
        except Exception as e:
            messagebox.showerror("Friendship", str(e)); return

        # 5) generate connkeys (best effort)
        try:
            requests.post(self.base.get().rstrip("/") + "/api/friends/connkey/generate",
                          json={"host": host_pub, "friend": me["public"]}, timeout=6)
        except Exception:
            pass

        # 6) launch (with HOME overridden to chosen keys home)
        env = os.environ.copy(); env["HOME"] = self.keys_home.get()
        ws = self.ws.get()
        try:
            if self.role.get() == "host":
                subprocess.Popen([sys.executable, "frontend/client.py", "host", "--ws", ws], env=env)
            else:
                subprocess.Popen([sys.executable, "frontend/client.py", "view", "--ws", ws, "--host", host_pub], env=env)
        except Exception as e:
            messagebox.showerror("Launch", str(e))

if __name__ == "__main__":
    App().mainloop()
