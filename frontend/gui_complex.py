import os, sys, json, subprocess, pathlib, tkinter as tk
from tkinter import ttk, messagebox
import requests
from datetime import datetime

KEYS_PATH = pathlib.Path.home() / ".liliumshare" / "keys.json"

def read_keys():
    if not KEYS_PATH.exists():
        messagebox.showerror("Keys", f"Keys not found at {KEYS_PATH}.\nRun: python frontend/keys.py --generate")
        raise RuntimeError("no keys")
    return json.loads(KEYS_PATH.read_text())

def ws_from_http(base: str) -> str:
    base = base.rstrip("/")
    return base.replace("http://","ws://").replace("https://","wss://") + "/ws"

class Tooltip:
    def __init__(self, widget, text):
        self.widget = widget; self.text = text; self.tw = None
        widget.bind("<Enter>", self._show); widget.bind("<Leave>", self._hide)
    def _show(self, _e):
        if self.tw: return
        x,y,_,_ = self.widget.bbox("insert") or (0,0,0,0)
        x += self.widget.winfo_rootx() + 20; y += self.widget.winfo_rooty() + 20
        self.tw = tk.Toplevel(self.widget); self.tw.wm_overrideredirect(True)
        lbl = ttk.Label(self.tw, text=self.text, background="#ffffe0", relief="solid", borderwidth=1)
        lbl.pack(ipadx=6, ipady=4); self.tw.wm_geometry(f"+{x}+{y}")
    def _hide(self, _e):
        if self.tw: self.tw.destroy(); self.tw=None

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LiliumShare • Advanced / Debug")
        self.geometry("900x600")
        self.base = tk.StringVar(value="http://localhost:8081")
        self.ws = tk.StringVar(value=ws_from_http(self.base.get()))
        self.nick = tk.StringVar(value="")
        self.me_pub = tk.StringVar(value="")
        self.friend_pub = tk.StringVar(value="")
        self.friend_nick = tk.StringVar(value="")
        self.perm_keyboard = tk.BooleanVar(value=True)
        self.perm_mouse = tk.BooleanVar(value=True)
        self.perm_controller = tk.BooleanVar(value=False)
        self.perm_immersion = tk.BooleanVar(value=False)
        self.audio_idx = tk.StringVar(value=os.environ.get("LILIUM_AUDIO_DEVICE",""))
        self._build()

    def log(self, *parts):
        now = datetime.now().strftime("%H:%M:%S")
        line = f"[{now}] " + " ".join(str(p) for p in parts)
        self.txt.configure(state="normal"); self.txt.insert("end", line+"\n"); self.txt.configure(state="disabled")
        self.txt.see("end")

    def _build(self):
        root = ttk.Frame(self, padding=10); root.pack(fill="both", expand=True)
        left = ttk.Frame(root); left.pack(side="left", fill="both", expand=True)
        right = ttk.Frame(root, width=340); right.pack(side="right", fill="y")

        # Left: controls
        row=0
        ttk.Label(left, text="Backend", font=("TkDefaultFont", 11, "bold")).grid(column=0, row=row, sticky="w"); row+=1
        fr = ttk.Frame(left); fr.grid(column=0, row=row, sticky="we"); row+=1
        ttk.Label(fr, text="Base URL (http)").grid(column=0, row=0, sticky="w")
        ttk.Entry(fr, textvariable=self.base, width=32).grid(column=1, row=0, sticky="we", padx=(6,6))
        b = ttk.Button(fr, text="Apply", command=self._apply_base); b.grid(column=2, row=0); Tooltip(b, "Set the signaling WS URL based on this HTTP base")
        ttk.Label(fr, textvariable=self.ws, foreground="#666").grid(column=1, row=1, columnspan=2, sticky="w", pady=(3,8))
        fr.columnconfigure(1, weight=1)
        ttk.Separator(left).grid(column=0, row=row, sticky="we", pady=6); row+=1

        ttk.Label(left, text="Identity", font=("TkDefaultFont", 11, "bold")).grid(column=0, row=row, sticky="w"); row+=1
        me = ttk.Frame(left); me.grid(column=0, row=row, sticky="we"); row+=1
        b = ttk.Button(me, text="Load My Keys", command=self._load_keys); b.grid(column=0, row=0, sticky="w"); Tooltip(b, "Read ~/.liliumshare/keys.json")
        ttk.Entry(me, textvariable=self.me_pub).grid(column=1, row=0, columnspan=2, sticky="we", padx=(6,0))
        ttk.Label(me, text="Nickname").grid(column=0, row=1, sticky="w", pady=(6,0))
        ttk.Entry(me, textvariable=self.nick, width=22).grid(column=1, row=1, sticky="w", padx=(6,6), pady=(6,0))
        b = ttk.Button(me, text="Register / Update", command=self._register_user); b.grid(column=2, row=1, sticky="w", pady=(6,0)); Tooltip(b, "POST /api/register")
        me.columnconfigure(1, weight=1)
        ttk.Separator(left).grid(column=0, row=row, sticky="we", pady=6); row+=1

        ttk.Label(left, text="Friend & Relationship", font=("TkDefaultFont", 11, "bold")).grid(column=0, row=row, sticky="w"); row+=1
        fr2 = ttk.Frame(left); fr2.grid(column=0, row=row, sticky="we"); row+=1
        ttk.Label(fr2, text="Friend nickname").grid(column=0, row=0, sticky="w")
        ttk.Entry(fr2, textvariable=self.friend_nick, width=22).grid(column=1, row=0, sticky="w", padx=(6,6))
        b = ttk.Button(fr2, text="Resolve nick → pubkey", command=self._resolve_friend); b.grid(column=2, row=0, sticky="w"); Tooltip(b, "GET /api/users/by-nickname")
        ttk.Label(fr2, text="Friend public key").grid(column=0, row=1, sticky="w", pady=(6,0))
        ttk.Entry(fr2, textvariable=self.friend_pub).grid(column=1, row=1, columnspan=2, sticky="we", padx=(6,0), pady=(6,0))
        fr2.columnconfigure(1, weight=1)

        # Permissions
        perms = ttk.LabelFrame(left, text="Permissions (apply on friendship)")
        perms.grid(column=0, row=row, sticky="we", pady=8); row+=1
        ttk.Checkbutton(perms, text="Keyboard", variable=self.perm_keyboard).grid(column=0, row=0, sticky="w")
        ttk.Checkbutton(perms, text="Mouse", variable=self.perm_mouse).grid(column=1, row=0, sticky="w")
        ttk.Checkbutton(perms, text="Controller", variable=self.perm_controller).grid(column=2, row=0, sticky="w")
        ttk.Checkbutton(perms, text="Immersion (Alt-Tab etc.)", variable=self.perm_immersion).grid(column=3, row=0, sticky="w")
        b = ttk.Button(perms, text="Apply permissions", command=self._apply_perms); b.grid(column=4, row=0, padx=6); Tooltip(b, "POST /api/friends/permissions (host=friend_pub, friend=me_pub)")

        # Relationship actions
        acts = ttk.Frame(left); acts.grid(column=0, row=row, sticky="we"); row+=1
        b = ttk.Button(acts, text="Send Friend Request", command=self._friend_request); b.grid(column=0, row=0, sticky="w"); Tooltip(b, "POST /api/friends/request")
        b = ttk.Button(acts, text="Accept Friendship (both directions)", command=self._accept_friend); b.grid(column=1, row=0, sticky="w", padx=(8,0)); Tooltip(b, "POST /api/friends/accept x2")
        b = ttk.Button(acts, text="Upsert (accept + perms)", command=self._upsert_friend); b.grid(column=2, row=0, sticky="w", padx=(8,0)); Tooltip(b, "POST /api/friends/upsert")
        b = ttk.Button(acts, text="Generate Connection Keys", command=self._gen_connkeys); b.grid(column=0, row=1, sticky="w", pady=(8,0)); Tooltip(b, "POST /api/friends/connkey/generate")
        b = ttk.Button(acts, text="Show Connection Keys", command=self._get_connkeys); b.grid(column=1, row=1, sticky="w", padx=(8,0), pady=(8,0)); Tooltip(b, "GET /api/friends/connkey")
        ttk.Separator(left).grid(column=0, row=row, sticky="we", pady=6); row+=1

        # Devices / launch
        launch = ttk.LabelFrame(left, text="Devices / Launch")
        launch.grid(column=0, row=row, sticky="we"); row+=1
        ttk.Label(launch, text="Audio device index (sounddevice) → env LILIUM_AUDIO_DEVICE").grid(column=0, row=0, columnspan=3, sticky="w")
        ttk.Entry(launch, textvariable=self.audio_idx, width=10).grid(column=0, row=1, sticky="w", pady=(4,6))
        b = ttk.Button(launch, text="List Audio Devices", command=self._list_audio); b.grid(column=1, row=1, sticky="w", padx=(8,0))
        ttk.Button(launch, text="Start HOST", command=self._start_host).grid(column=0, row=2, pady=6, sticky="w")
        ttk.Button(launch, text="Start VIEWER", command=self._start_viewer).grid(column=1, row=2, pady=6, sticky="w")
        left.columnconfigure(0, weight=1)

        # Right: log
        ttk.Label(right, text="Log").pack(anchor="w")
        self.txt = tk.Text(right, width=48, height=30, state="disabled")
        self.txt.pack(fill="both", expand=True)

    def _apply_base(self): self.ws.set(ws_from_http(self.base.get().strip()))

    def _load_keys(self):
        try:
            k = read_keys()
            self.me_pub.set(k["public"])
            if k.get("nickname"): self.nick.set(k["nickname"])
            self.log("Loaded keys")
        except Exception: pass

    def _register_user(self):
        try:
            k = read_keys()
            r = requests.post(self.base.get().rstrip("/") + "/api/register",
                              json={"pubkey": k["public"], "nickname": self.nick.get().strip() or None}, timeout=8)
            self.log("register:", r.status_code, r.text)
        except Exception as e:
            messagebox.showerror("Register", str(e))

    def _resolve_friend(self):
        try:
            r = requests.get(self.base.get().rstrip("/") + "/api/users/by-nickname",
                             params={"nickname": self.friend_nick.get().strip()}, timeout=8)
            r.raise_for_status()
            self.friend_pub.set(r.json()["pubkey"])
            self.log("resolve:", self.friend_nick.get(), "→", self.friend_pub.get())
        except Exception as e:
            messagebox.showerror("Resolve", str(e))

    def _friend_request(self):
        try:
            k = read_keys()
            r = requests.post(self.base.get().rstrip("/") + "/api/friends/request",
                              json={"me": k["public"], "friend": self.friend_pub.get().strip()}, timeout=8)
            self.log("friend request:", r.status_code, r.text)
        except Exception as e:
            messagebox.showerror("Friend Request", str(e))

    def _accept_friend(self):
        try:
            k = read_keys()
            r = requests.post(self.base.get().rstrip("/") + "/api/friends/accept",
                              json={"me": k["public"], "friend": self.friend_pub.get().strip()}, timeout=8)
            self.log("accept:", r.status_code, r.text)
        except Exception as e:
            messagebox.showerror("Accept", str(e))

    def _upsert_friend(self):
        try:
            k = read_keys()
            perms = {
                "keyboard": self.perm_keyboard.get(),
                "mouse": self.perm_mouse.get(),
                "controller": self.perm_controller.get(),
                "immersion": self.perm_immersion.get(),
                "autoJoin": True,
            }
            r = requests.post(self.base.get().rstrip("/") + "/api/friends/upsert",
                              json={"host": self.friend_pub.get().strip(), "friend": k["public"], "permissions": perms}, timeout=8)
            self.log("upsert:", r.status_code, r.text)
        except Exception as e:
            messagebox.showerror("Upsert", str(e))

    def _apply_perms(self):
        try:
            perms = {
                "keyboard": self.perm_keyboard.get(),
                "mouse": self.perm_mouse.get(),
                "controller": self.perm_controller.get(),
                "immersion": self.perm_immersion.get(),
                "autoJoin": True,
            }
            r = requests.post(self.base.get().rstrip("/") + "/api/friends/permissions",
                              json={"host": self.friend_pub.get().strip(), "friend": self.me_pub.get().strip(), "permissions": perms}, timeout=8)
            self.log("perms:", r.status_code, r.text)
        except Exception as e:
            messagebox.showerror("Permissions", str(e))

    def _gen_connkeys(self):
        try:
            r = requests.post(self.base.get().rstrip("/") + "/api/friends/connkey/generate",
                              json={"host": self.friend_pub.get().strip(), "friend": self.me_pub.get().strip()}, timeout=8)
            self.log("connkey generate:", r.status_code, r.text)
        except Exception as e:
            messagebox.showerror("Conn Keys", str(e))

    def _get_connkeys(self):
        try:
            r = requests.get(self.base.get().rstrip("/") + "/api/friends/connkey",
                             params={"host": self.friend_pub.get().strip(), "friend": self.me_pub.get().strip()}, timeout=8)
            self.log("connkey get:", r.status_code, r.text)
            if r.ok:
                messagebox.showinfo("Connection keys", r.text)
        except Exception as e:
            messagebox.showerror("Conn Keys", str(e))

    def _list_audio(self):
        try:
            import sounddevice as sd
            devs = sd.query_devices()
            msg = "\n".join([f"{i}: {d['name']} (in={int(d.get('max_input_channels',0))} / out={int(d.get('max_output_channels',0))})" for i,d in enumerate(devs)])
            messagebox.showinfo("Audio Devices", msg if msg else "No devices")
        except Exception as e:
            messagebox.showerror("Devices", str(e))

    def _start_host(self):
        if self.audio_idx.get(): os.environ["LILIUM_AUDIO_DEVICE"] = self.audio_idx.get()
        ws = self.ws.get()
        try:
            subprocess.Popen([sys.executable, "frontend/client.py", "host", "--ws", ws])
            self.log("launched host")
        except Exception as e:
            messagebox.showerror("Start Host", str(e))

    def _start_viewer(self):
        ws = self.ws.get()
        host = self.friend_pub.get().strip()
        if not host:
            messagebox.showerror("Start Viewer", "Friend PubKey required (Host)."); return
        try:
            subprocess.Popen([sys.executable, "frontend/client.py", "view", "--ws", ws, "--host", host])
            self.log("launched viewer →", host)
        except Exception as e:
            messagebox.showerror("Start Viewer", str(e))

if __name__ == "__main__":
    App().mainloop()
