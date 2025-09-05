#!/usr/bin/env python3
import os, sys, json, time, pathlib, subprocess, threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import requests

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

def ws_from_http(base: str) -> str:
    base = base.rstrip("/")
    return base.replace("http://","ws://").replace("https://","wss://") + "/ws"

def load_keys(home: pathlib.Path):
    kf = home / ".liliumshare" / "keys.json"
    return json.loads(kf.read_text())

def short(s, n=12): return s[:n] + "…" if s and len(s)>n else s

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LiliumShare — Advanced")
        self.geometry("1100x680")

        self.base = tk.StringVar(value=DEFAULT_HTTP_BASE)
        self.ws   = tk.StringVar(value=DEFAULT_WS_BASE)
        self.keys_home = tk.StringVar(value=str(pathlib.Path.home()))
        self.me_pub = tk.StringVar(value="")
        self.friend_pub = tk.StringVar(value="")
        self.me_nick = tk.StringVar(value="")
        self.friend_nick = tk.StringVar(value="")
        self.audio_choice = tk.StringVar(value="Default")
        self.video_choice = tk.StringVar(value="Portal (Screen)")

        self._build()
        self._refresh_audio(); self._refresh_video()
        self._reload_lists()

    def _build(self):
        top = ttk.Frame(self); top.pack(fill="x", padx=8, pady=6)
        ttk.Label(top, text="Backend:").grid(row=0,column=0); ttk.Entry(top, textvariable=self.base, width=32).grid(row=0,column=1)
        ttk.Button(top, text="Apply", command=self._apply).grid(row=0,column=2, padx=6)
        ttk.Label(top, text="WS:").grid(row=0,column=3, padx=(18,2)); ttk.Entry(top, textvariable=self.ws, state="readonly", width=32).grid(row=0,column=4)

        ttk.Label(top, text="Keys Home:").grid(row=0,column=5, padx=(18,2))
        ttk.Entry(top, textvariable=self.keys_home, width=30).grid(row=0,column=6)
        ttk.Button(top, text="Browse…", command=self._pick_home).grid(row=0,column=7, padx=4)
        ttk.Button(top, text="Load Keys", command=self._load_keys).grid(row=0,column=8, padx=4)
        top.grid_columnconfigure(6, weight=1)

        main = ttk.PanedWindow(self, orient="horizontal"); main.pack(fill="both", expand=True, padx=8, pady=8)
        left = ttk.Frame(main); right = ttk.Frame(main); main.add(left, weight=1); main.add(right, weight=3)

        # Left lists
        self.tree = ttk.Treeview(left, columns=("nick","pub"), show="tree")
        self.grp_in  = self.tree.insert("", "end", text="Incoming", open=True)
        self.grp_out = self.tree.insert("", "end", text="Outgoing", open=True)
        self.grp_acc = self.tree.insert("", "end", text="Friends", open=True)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._sel)
        ttk.Button(left, text="Refresh", command=self._reload_lists).pack(fill="x", pady=6)

        # Right controls
        top2 = ttk.LabelFrame(right, text="User")
        top2.pack(fill="x", padx=6, pady=6)
        ttk.Label(top2, text="Me (nick)").grid(row=0,column=0, sticky="e"); ttk.Entry(top2, textvariable=self.me_nick, width=20).grid(row=0,column=1, sticky="w")
        ttk.Label(top2, text="Me (pubkey)").grid(row=1,column=0, sticky="e"); ttk.Entry(top2, textvariable=self.me_pub, width=70).grid(row=1,column=1, sticky="we", columnspan=3)
        ttk.Button(top2, text="Register/Update", command=self._register_user).grid(row=0,column=2, padx=6)
        top2.grid_columnconfigure(1, weight=1)

        friend = ttk.LabelFrame(right, text="Friend Ops")
        friend.pack(fill="x", padx=6, pady=6)
        ttk.Label(friend, text="Friend nick").grid(row=0,column=0, sticky="e")
        ttk.Entry(friend, textvariable=self.friend_nick, width=24).grid(row=0,column=1, sticky="w")
        ttk.Button(friend, text="Resolve", command=self._resolve).grid(row=0,column=2, padx=4)
        ttk.Label(friend, text="Friend pubkey").grid(row=1,column=0, sticky="e")
        ttk.Entry(friend, textvariable=self.friend_pub, width=70).grid(row=1,column=1, columnspan=3, sticky="we")
        ttk.Button(friend, text="Send Friend Request", command=self._request).grid(row=2,column=0, pady=4)
        ttk.Button(friend, text="Accept (both ways)", command=self._accept_both).grid(row=2,column=1, pady=4)
        ttk.Button(friend, text="Generate Conn Key", command=self._gen_connkey).grid(row=2,column=2, pady=4)
        ttk.Button(friend, text="Show Conn Key", command=self._show_connkey).grid(row=2,column=3, pady=4)

        dev = ttk.LabelFrame(right, text="Devices / Launch")
        dev.pack(fill="x", padx=6, pady=6)
        ttk.Label(dev, text="Audio").grid(row=0,column=0, sticky="e")
        self.cmb_audio = ttk.Combobox(dev, textvariable=self.audio_choice, state="readonly", width=40); self.cmb_audio.grid(row=0,column=1, sticky="w")
        ttk.Button(dev, text="Refresh", command=self._refresh_audio).grid(row=0,column=2, padx=4)

        ttk.Label(dev, text="Video").grid(row=1,column=0, sticky="e")
        self.cmb_video = ttk.Combobox(dev, textvariable=self.video_choice, state="readonly", width=40); self.cmb_video.grid(row=1,column=1, sticky="w")
        ttk.Button(dev, text="Refresh", command=self._refresh_video).grid(row=1,column=2, padx=4)

        ttk.Button(dev, text="Start HOST", command=self._start_host).grid(row=2,column=0, pady=6)
        ttk.Button(dev, text="Start VIEWER", command=self._start_viewer).grid(row=2,column=1, pady=6)

        # Log
        logf = ttk.LabelFrame(right, text="Log")
        logf.pack(fill="both", expand=True, padx=6, pady=6)
        self.log = tk.Text(logf, height=10); self.log.pack(fill="both", expand=True)

    # helpers
    def _log(self, s): self.log.insert("end", s.rstrip()+"\n"); self.log.see("end")
    def _apply(self): self.ws.set(ws_from_http(self.base.get()))
    def _pick_home(self):
        path = filedialog.askdirectory(initialdir=self.keys_home.get() or str(pathlib.Path.home()))
        if path: self.keys_home.set(path)
    def _load_keys(self):
        try:
            k = load_keys(pathlib.Path(self.keys_home.get()))
        except Exception as e:
            messagebox.showerror("Keys", str(e)); return
        self.me_pub.set(k.get("public","")); self.me_nick.set(k.get("nickname") or "")
        self._log(f"Loaded keys: {short(self.me_pub.get(),20)}")

    def _api(self, m, p, **kw):
        r = requests.request(m, self.base.get().rstrip("/")+p, timeout=8, **kw)
        return r

    def _reload_lists(self):
        if not self.me_pub.get(): return
        try:
            r = self._api("GET","/api/friends/list", params={"me": self.me_pub.get()})
            r.raise_for_status()
            d = r.json()
        except Exception as e:
            self._log(f"list error: {e}"); return
        for grp in (self.grp_in, self.grp_out, self.grp_acc):
            for c in self.tree.get_children(grp): self.tree.delete(c)
        for row in d.get("incoming", []):
            iid = self.tree.insert(self.grp_in, "end", text=row.get("nickname") or short(row["viewer_pubkey"]), open=False)
            self.tree.item(iid, values=(row.get("nickname") or "", row["viewer_pubkey"]))
        for row in d.get("outgoing", []):
            iid = self.tree.insert(self.grp_out, "end", text=row.get("nickname") or short(row["host_pubkey"]), open=False)
            self.tree.item(iid, values=(row.get("nickname") or "", row["host_pubkey"]))
        for row in d.get("accepted", []):
            iid = self.tree.insert(self.grp_acc, "end", text=row.get("nickname") or short(row["pubkey"]), open=False)
            self.tree.item(iid, values=(row.get("nickname") or "", row["pubkey"]))

    def _sel(self, _):
        sel = self.tree.selection()
        if not sel: return
        iid = sel[0]
        vals = self.tree.item(iid,"values")
        if len(vals)>=2:
            self.friend_nick.set(vals[0]); self.friend_pub.set(vals[1])

    # friend ops
    def _register_user(self):
         try:
             r = self._api("POST","/api/register",
                           json={"pubkey": self.me_pub.get(), "nickname": self.me_nick.get()})
             self._log(f"register: {r.status_code} {r.text}")
         except Exception as e:
             self._log(f"register error: {e}")

    def _resolve(self):
        try:
            r = self._api("GET","/api/users/by-nickname", params={"nickname": self.friend_nick.get()})
            r.raise_for_status()
            self.friend_pub.set(r.json()["pubkey"])
            self._log(f"resolve: {self.friend_nick.get()} -> {short(self.friend_pub.get(),18)}")
        except Exception as e:
            self._log(f"resolve error: {e}")

    def _request(self):
        try:
            r = self._api("POST","/api/friends/request",
                          json={"me": self.me_pub.get(), "friend": self.friend_pub.get()})
            self._log(f"request: {r.status_code} {r.text}")
            self._reload_lists()
        except Exception as e:
            self._log(f"request error: {e}")

    def _accept_both(self):
        perms = {"autoJoin": True, "keyboard": True, "mouse": True, "controller": False, "immersion": False}
        try:
            r = self._api("POST","/api/friends/upsert",
                          json={"host": self.me_pub.get(), "friend": self.friend_pub.get(), "permissions": perms})
            self._log(f"accept both: {r.status_code} {r.text}")
            self._reload_lists()
        except Exception as e:
            self._log(f"accept error: {e}")

    def _gen_connkey(self):
        try:
            r = self._api("POST","/api/friends/connkey/generate",
                          json={"host": self.me_pub.get(), "friend": self.friend_pub.get()})
            self._log(f"gen connkey: {r.status_code} {r.text}")
        except Exception as e:
            self._log(f"gen connkey error: {e}")

    def _show_connkey(self):
        try:
            r = self._api("GET","/api/friends/connkey",
                          params={"host": self.me_pub.get(), "friend": self.friend_pub.get()})
            self._log(f"connkey: {r.status_code} {r.text}")
        except Exception as e:
            self._log(f"get connkey error: {e}")

    def _refresh_audio(self):
        try:
            import sounddevice as sd
            devs = sd.query_devices()
            items = ["Default"] + [f"{i}: {d['name']}" for i,d in enumerate(devs)]
            if self.audio_choice.get() not in items: self.audio_choice.set(items[0])
            self.audio_list = items; self.cmb_audio = getattr(self, "cmb_audio", None)
            if self.cmb_audio: self.cmb_audio["values"] = items
        except Exception:
            self.audio_list = ["Default"]

    def _refresh_video(self):
        items = ["Portal (Screen)", "Synthetic"]
        try:
            import cv2
            for idx in range(0,5):
                cap = cv2.VideoCapture(idx, cv2.CAP_ANY)
                if cap and cap.isOpened(): items.append(f"Camera {idx}")
                if cap: cap.release()
        except Exception: pass
        self.video_list = items
        self.cmb_video = getattr(self, "cmb_video", None)
        if self.cmb_video: self.cmb_video["values"] = items
        if self.video_choice.get() not in items: self.video_choice.set(items[0])

    def _env_devices(self):
        env = os.environ.copy()
        env["HOME"] = self.keys_home.get()
        if self.audio_choice.get() != "Default" and ":" in self.audio_choice.get():
            env["LILIUM_AUDIO_DEVICE"] = self.audio_choice.get().split(":")[0]
        if self.video_choice.get().startswith("Portal"):
            env["LILIUM_VIDEO_MODE"] = "portal"
        elif self.video_choice.get().startswith("Synthetic"):
            env["LILIUM_VIDEO_MODE"] = "synthetic"
        else:
            env["LILIUM_VIDEO_MODE"] = "camera"
            env["LILIUM_CAMERA_INDEX"] = self.video_choice.get().split()[-1]
        return env

    def _start_host(self):
        try:
            env = self._env_devices()
            subprocess.Popen([sys.executable, "frontend/client.py", "host", "--ws", self.ws.get()], env=env)
            self._log("HOST started.")
        except Exception as e:
            self._log(f"host error: {e}")

    def _start_viewer(self):
        try:
            env = self._env_devices()
            host = self.friend_pub.get()
            if not host: messagebox.showerror("Viewer","Friend pubkey required"); return
            subprocess.Popen([sys.executable, "frontend/client.py", "view", "--ws", self.ws.get(), "--host", host], env=env)
            self._log("VIEWER started.")
        except Exception as e:
            self._log(f"viewer error: {e}")

if __name__ == "__main__":
    App().mainloop()
