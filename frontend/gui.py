#!/usr/bin/env python3
import os, sys, json, pathlib, subprocess
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

KEYS_NAME = "keys.json"

def ws_from_http(base: str) -> str:
    base = base.rstrip("/")
    return base.replace("http://","ws://").replace("https://","wss://") + "/ws"

def load_keys(keys_home: pathlib.Path):
    kf = keys_home / ".liliumshare" / KEYS_NAME
    if not kf.exists():
        raise FileNotFoundError(f"Missing keys at {kf}")
    return json.loads(kf.read_text())

def short(pk: str, n=10):
    return pk[:n] + "…" if pk and len(pk) > n else pk

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LiliumShare")
        self.geometry("980x640")
        self.minsize(880, 560)

        self.base = tk.StringVar(value=DEFAULT_HTTP_BASE)
        self.ws   = tk.StringVar(value=DEFAULT_WS_BASE)
        self.keys_home = tk.StringVar(value=str(pathlib.Path.home()))
        self.me_pub = tk.StringVar(value="")
        self.me_nick = tk.StringVar(value="")
        self.audio_choice = tk.StringVar(value="Default")
        self.video_choice = tk.StringVar(value="Portal (Screen)")
        self.status_map = {}   # pubkey -> {'state': 'incoming|outgoing|accepted', 'blink':bool, 'item':iid}
        self._blink_phase = True

        self._build_ui()
        self._start_poll()

    # ---------------- UI build ----------------
    def _build_ui(self):
        top = ttk.Frame(self); top.pack(fill="x", padx=8, pady=6)
        ttk.Label(top, text="Backend:").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.base, width=34).grid(row=0, column=1, sticky="w")
        ttk.Button(top, text="Apply", command=self._apply_base).grid(row=0, column=2, padx=6)

        ttk.Label(top, text="WS:").grid(row=0, column=3, sticky="w", padx=(18,2))
        ttk.Entry(top, textvariable=self.ws, width=34, state="readonly").grid(row=0, column=4, sticky="w")

        ttk.Label(top, text="Keys Home:").grid(row=0, column=5, sticky="e", padx=(18,2))
        ttk.Entry(top, textvariable=self.keys_home, width=28).grid(row=0, column=6, sticky="we")
        ttk.Button(top, text="Browse…", command=self._pick_home).grid(row=0, column=7, padx=4)
        ttk.Button(top, text="Load Keys", command=self._load_my_keys).grid(row=0, column=8, padx=4)
        top.grid_columnconfigure(6, weight=1)

        body = ttk.Frame(self); body.pack(fill="both", expand=True, padx=8, pady=(0,8))
        left = ttk.Frame(body); right = ttk.Frame(body)
        left.pack(side="left", fill="y")
        right.pack(side="right", fill="both", expand=True)

        self.tree = ttk.Treeview(left, columns=("nick","pub"), show="tree")
        self.tree.pack(fill="y", expand=False, side="left")
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        sb.pack(side="left", fill="y")
        self.tree.configure(yscrollcommand=sb.set)

        self.grp_in = self.tree.insert("", "end", text="Incoming requests", open=True)
        self.grp_out = self.tree.insert("", "end", text="Outgoing requests", open=True)
        self.grp_acc = self.tree.insert("", "end", text="Friends", open=True)

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Button-3>", self._on_right_click)

        style = ttk.Style(self)
        style.map("Treeview", background=[('selected', '#335577')])
        self.tree.tag_configure("incoming1", background="#FFF3B0")
        self.tree.tag_configure("incoming2", background="#FFE070")
        self.tree.tag_configure("outgoing1", background="#B0D8FF")
        self.tree.tag_configure("outgoing2", background="#70BEFF")
        self.tree.tag_configure("accepted", background="")

        info = ttk.LabelFrame(right, text="Friend")
        info.pack(fill="x", padx=6, pady=6)
        self.sel_nick = tk.StringVar(value="")
        self.sel_pub  = tk.StringVar(value="")
        ttk.Label(info, text="Nickname:").grid(row=0, column=0, sticky="e")
        ttk.Entry(info, textvariable=self.sel_nick, state="readonly").grid(row=0, column=1, sticky="we", padx=6)
        ttk.Label(info, text="PubKey:").grid(row=1, column=0, sticky="e")
        ttk.Entry(info, textvariable=self.sel_pub, state="readonly").grid(row=1, column=1, sticky="we", padx=6)
        info.grid_columnconfigure(1, weight=1)

        dev = ttk.LabelFrame(right, text="Devices")
        dev.pack(fill="x", padx=6, pady=6)
        ttk.Label(dev, text="Audio:").grid(row=0, column=0, sticky="e")
        self.cmb_audio = ttk.Combobox(dev, textvariable=self.audio_choice, state="readonly", width=40)
        self.cmb_audio.grid(row=0, column=1, sticky="w", padx=6)
        ttk.Button(dev, text="Refresh", command=self._refresh_audio).grid(row=0, column=2, padx=4)

        ttk.Label(dev, text="Video:").grid(row=1, column=0, sticky="e")
        self.cmb_video = ttk.Combobox(dev, textvariable=self.video_choice, state="readonly", width=40)
        self.cmb_video.grid(row=1, column=1, sticky="w", padx=6)
        ttk.Button(dev, text="Refresh", command=self._refresh_video).grid(row=1, column=2, padx=4)

        act = ttk.Frame(right); act.pack(fill="x", padx=6, pady=6)
        ttk.Button(act, text="Connect (view friend)", command=self._connect_view).pack(side="left")
        ttk.Button(act, text="Share my screen (host)", command=self._start_host).pack(side="left", padx=8)
        ttk.Button(act, text="Settings…", command=self._open_settings).pack(side="left", padx=8)

        self._refresh_audio()
        self._refresh_video()
        self._reload_lists()

    # ---------- helpers ----------
    def _apply_base(self):
        self.ws.set(ws_from_http(self.base.get()))

    def _pick_home(self):
        path = filedialog.askdirectory(initialdir=self.keys_home.get() or str(pathlib.Path.home()))
        if path:
            self.keys_home.set(path)

    def _load_my_keys(self):
        try:
            k = load_keys(pathlib.Path(self.keys_home.get()))
        except Exception as e:
            messagebox.showerror("Keys", str(e)); return
        self.me_pub.set(k.get("public",""))
        self.me_nick.set(k.get("nickname") or "")
        messagebox.showinfo("Keys", f"Loaded.\nNick: {self.me_nick.get()}\nPub: {short(self.me_pub.get(), 18)}")
        self._reload_lists()

    def _api(self, method, path, **kw):
        url = self.base.get().rstrip("/") + path
        return requests.request(method, url, timeout=8, **kw)

    # ---------- list / blinking ----------
    def _reload_lists(self):
        me = self.me_pub.get().strip()
        if not me:
            return
        try:
            r = self._api("GET", "/api/friends/list", params={"me": me})
            r.raise_for_status()
            data = r.json()
        except Exception:
            return

        # clear groups
        for grp in (self.grp_in, self.grp_out, self.grp_acc):
            for ch in self.tree.get_children(grp):
                self.tree.delete(ch)
        self.status_map.clear()

        # incoming: [{other, nickname,...}]
        for row in data.get("incoming", []):
            pk = row["other"]; nick = row.get("nickname") or short(pk)
            iid = self.tree.insert(self.grp_in, "end", text=f"{nick}  ({short(pk,12)})", tags=("incoming1",))
            self.status_map[pk] = {"state":"incoming", "blink":True, "item":iid}

        # outgoing
        for row in data.get("outgoing", []):
            pk = row["other"]; nick = row.get("nickname") or short(pk)
            iid = self.tree.insert(self.grp_out, "end", text=f"{nick}  ({short(pk,12)})", tags=("outgoing1",))
            self.status_map[pk] = {"state":"outgoing", "blink":True, "item":iid}

        # friends
        for row in data.get("friends", []):
            pk = row["other"]; nick = row.get("nickname") or short(pk)
            iid = self.tree.insert(self.grp_acc, "end", text=f"{nick}  ({short(pk,12)})", tags=("accepted",))
            self.status_map[pk] = {"state":"accepted", "blink":False, "item":iid}

        self.tree.selection_remove(self.tree.selection())
        self.sel_pub.set(""); self.sel_nick.set("")

    def _blink_tick(self):
        self._blink_phase = not self._blink_phase
        for pk, st in self.status_map.items():
            if not st["blink"]: continue
            tag = None
            if st["state"] == "incoming":
                tag = "incoming1" if self._blink_phase else "incoming2"
            elif st["state"] == "outgoing":
                tag = "outgoing1" if self._blink_phase else "outgoing2"
            if tag:
                self.tree.item(st["item"], tags=(tag,))
        self.after(600, self._blink_tick)

    def _start_poll(self):
        self.after(850, self._reload_lists)
        self._blink_tick()

    # ---------- selection & context ----------
    def _selected_pk_from_click(self):
        sel = self.tree.selection()
        if not sel: return None
        iid = sel[0]
        # reverse-lookup pk by iid
        for pk, st in self.status_map.items():
            if st["item"] == iid: return pk
        return None

    def _on_select(self, _):
        pk = self._selected_pk_from_click()
        if not pk:
            return
        self.sel_pub.set(pk)
        self.sel_nick.set("")

    def _on_right_click(self, ev):
        iid = self.tree.identify_row(ev.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        pk = self._selected_pk_from_click()
        if not pk:
            return
        st = self.status_map.get(pk, {})
        menu = tk.Menu(self, tearoff=0)
        if st.get("state") == "incoming":
            menu.add_command(label="Accept request", command=lambda: self._accept(pk))
            menu.add_command(label="Decline request", command=lambda: self._decline(pk))
        elif st.get("state") == "outgoing":
            menu.add_command(label="Cancel request", command=lambda: self._cancel(pk))
        else:
            menu.add_command(label="Connect (view friend)", command=self._connect_view)
            menu.add_command(label="Share my screen (host)", command=self._start_host)
            menu.add_separator()
            menu.add_command(label="Settings…", command=self._open_settings)
        try:
            menu.tk_popup(ev.x_root, ev.y_root)
        finally:
            menu.grab_release()


    # ---------- backend ops ----------
    def _accept(self, other):
        me = self.me_pub.get().strip()
        if not me: 
            messagebox.showerror("Accept", "Load your keys first."); return
        try:
            r = self._api("POST", "/api/friends/accept", json={"me": me, "friend": other})
            r.raise_for_status()
        except Exception as e:
            messagebox.showerror("Accept", str(e))
        self._reload_lists()

    def _decline(self, other):
        # Backend doesn’t expose a real delete yet; do a soft-decline and refresh.
        try:
            self._api("POST", "/api/friends/permissions",
                    json={"host": self.me_pub.get().strip(), "friend": other, "permissions": {}})
        except Exception:
            pass
        self._reload_lists()

    def _cancel(self, host_pubkey):
        messagebox.showinfo("Cancel", "No cancel endpoint yet; ignoring outgoing request.")
        self._reload_lists()


    # ---------- devices ----------
    def _refresh_audio(self):
        try:
            import sounddevice as sd
            devs = sd.query_devices()
            items = ["Default"]
            for i, d in enumerate(devs):
                items.append(f"{i}: {d['name']}")
            self.cmb_audio["values"] = items
            if self.audio_choice.get() not in items:
                self.audio_choice.set(items[0] if items else "Default")
        except Exception:
            self.cmb_audio["values"] = ["Default"]
            self.audio_choice.set("Default")

    def _refresh_video(self):
        items = ["Portal (Screen)", "Synthetic"]
        try:
            import cv2
            for idx in range(0, 5):
                cap = cv2.VideoCapture(idx, cv2.CAP_ANY)
                if cap and cap.isOpened():
                    items.append(f"Camera {idx}")
                if cap: cap.release()
        except Exception:
            pass
        self.cmb_video["values"] = items
        if self.video_choice.get() not in items:
            self.video_choice.set(items[0] if items else "Portal (Screen)")

    # ---------- launchers ----------
    def _connect_view(self):
        host_pub = self.sel_pub.get().strip()
        if not host_pub:
            messagebox.showerror("Connect", "Select a friend first."); return
        env = os.environ.copy()
        env["HOME"] = self.keys_home.get()
        if self.audio_choice.get() != "Default" and ":" in self.audio_choice.get():
            env["LILIUM_AUDIO_DEVICE"] = self.audio_choice.get().split(":")[0]
        vc = self.video_choice.get()
        if vc.startswith("Portal"):
            env["LILIUM_VIDEO_MODE"] = "portal"
        elif vc.startswith("Synthetic"):
            env["LILIUM_VIDEO_MODE"] = "synthetic"
        elif vc.startswith("Camera"):
            env["LILIUM_VIDEO_MODE"] = "camera"
            env["LILIUM_CAMERA_INDEX"] = vc.split()[-1]
        try:
            subprocess.Popen([sys.executable, "frontend/client.py", "view", "--ws", self.ws.get(), "--host", host_pub], env=env)
        except Exception as e:
            messagebox.showerror("Connect", str(e))

    def _start_host(self):
        env = os.environ.copy()
        env["HOME"] = self.keys_home.get()
        if self.audio_choice.get() != "Default" and ":" in self.audio_choice.get():
            env["LILIUM_AUDIO_DEVICE"] = self.audio_choice.get().split(":")[0]
        vc = self.video_choice.get()
        if vc.startswith("Portal"):
            env["LILIUM_VIDEO_MODE"] = "portal"
        elif vc.startswith("Synthetic"):
            env["LILIUM_VIDEO_MODE"] = "synthetic"
        elif vc.startswith("Camera"):
            env["LILIUM_VIDEO_MODE"] = "camera"
            env["LILIUM_CAMERA_INDEX"] = vc.split()[-1]
        try:
            subprocess.Popen([sys.executable, "frontend/client.py", "host", "--ws", self.ws.get()], env=env)
        except Exception as e:
            messagebox.showerror("Host", str(e))

    # ---------- settings popup ----------
    def _open_settings(self):
        pk = self.sel_pub.get().strip()
        if not pk:
            messagebox.showerror("Settings", "Select a friend first."); return
        me = self.me_pub.get().strip()
        if not me:
            messagebox.showerror("Settings", "Load your keys first."); return

        w = tk.Toplevel(self); w.title(f"Settings — {short(pk,12)}")
        frm = ttk.LabelFrame(w, text="Permissions (from YOU → them)")
        frm.pack(fill="x", padx=8, pady=8)
        v_k = tk.BooleanVar(value=True); v_m = tk.BooleanVar(value=True)
        v_c = tk.BooleanVar(value=False); v_i = tk.BooleanVar(value=False)

        ttk.Checkbutton(frm, text="Keyboard",  variable=v_k).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(frm, text="Mouse",     variable=v_m).grid(row=0, column=1, sticky="w")
        ttk.Checkbutton(frm, text="Controller",variable=v_c).grid(row=0, column=2, sticky="w")
        ttk.Checkbutton(frm, text="Immersion", variable=v_i).grid(row=0, column=3, sticky="w")

        def apply_perms():
            perms = {"keyboard": v_k.get(), "mouse": v_m.get(), "controller": v_c.get(), "immersion": v_i.get(), "autoJoin": True}
            try:
                r = self._api("POST", "/api/friends/permissions", json={"host": pk, "friend": me, "permissions": perms})
                messagebox.showinfo("Permissions", r.text if hasattr(r,'text') else "OK")
            except Exception as e:
                messagebox.showerror("Permissions", str(e))

        ttk.Button(frm, text="Apply", command=apply_perms).grid(row=1, column=0, pady=8, sticky="w")
        ttk.Button(frm, text="Close", command=w.destroy).grid(row=1, column=1, pady=8, sticky="w")

if __name__ == "__main__":
    App().mainloop()
