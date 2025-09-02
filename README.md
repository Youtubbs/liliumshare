# LiliumShare (local prototype)

A minimal, local-first, Parsec-like screen sharing prototype with input permissions and audio passthrough.

- **Backend:** Node.js (Express + WebSocket) + PostgreSQL 
- **Frontend:** Python (aiortc) for host/viewer; screen capture (mss), input (pynput), audio (sounddevice) 
- **Identity / friending:** Users are identified by **Ed25519 public keys** (base64). Friendship is host→viewer with an `accepted` status and per-viewer permissions (auto-join, keyboard, mouse, controller, immersion).

---

## Architecture

- **Postgres**: stores `users` and `friendships (host_pubkey, friend_pubkey, status, permissions)` 
- **Node backend**: REST for registration/friending/permissions + **WebSocket signaling** at `/ws` 
- **Python host/viewer**: set up WebRTC; host captures screen+audio, viewer renders stream

> Default backend port (host): **8081** → REST `http://localhost:8081`, WS `ws://localhost:8081/ws`.

---

## Prerequisites

- Docker & Docker Compose
- Python **3.10+**
- (Linux) You may need `libgl1`, `libglib2.0-0`, etc., for OpenCV GUI; see your distro docs if a viewer window won’t open.

---

## Quick start

### 1) Start services

```bash
docker compose up -d --build
curl http://localhost:8081/health  # → {"ok":true}
```
The backend auto-migrates the DB on startup.

### 2) Python env

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: I think it's .venv\Scripts\activate
pip install -r frontend/requirements.txt
```

### 3) Demo: Create (or reuse) two local users and wire friendship

```bash 
python scripts/bootstrap_local_dual_user.py \
  --base http://localhost:8080 \
  --ahome "$(pwd)/_userA_home" \
  --bhome "$(pwd)/_userB_home"
```
The script:
- ensures keys exist at <HOME>/.liliumshare/keys.json for HostA and ViewerB,
- registers both users,
- upserts friendship (accepted) and permissions (autoJoin + kb/mouse),
- prints A_PUB (host pubkey) and the exact commands to run host & viewer.
Re-running the script reuses the same keys; pass --force-new-keys if you intentionally want fresh identities (then restart the clients).

Note that you may have to change 8080 to 8081 in the resulting run commands outputted by the python script. More troubleshooting required to understand why.

```bash
docker compose logs -f backend
```

Don't forget to start up logging for your server.

### 4) Connecting two users
```bash 
# Terminal 1 (HOST)
HOME="$(pwd)/_userA_home" python frontend/client.py host

# Terminal 2 (VIEWER)
HOME="$(pwd)/_userB_home" python frontend/client.py view --host "<A_PUB_FROM_BOOTSTRAP>"
```
press 'q' in viewer window to quit

---

### REST API (backend)

Base URL: http://localhost:8081 (use $BASE below)

### Health

```bash
GET $BASE/health
→ {"ok":true}
```

### Register user

```bash
POST $BASE/api/register
Content-Type: application/json
{
  "pubkey": "<base64 ed25519 public key>",
  "nickname": "Alice"   // optional
}
→ { "ok": true }
```

### Friend Request (Viewer -> Host)

```bash
POST $BASE/api/friends/request
{
  "me":     "<viewer_pubkey>",
  "friend": "<host_pubkey>"
}
→ { "ok": true }
```

### Accept friendship (creates/updates both directions)

```bash 
POST $BASE/api/friends/accept
{
  "me":     "<your_pubkey>",
  "friend": "<their_pubkey>"
}
→ { "ok": true }
```

### Set permissions (host → viewer)

```bash 
POST $BASE/api/friends/permissions
{
  "host": "<host_pubkey>",
  "friend": "<viewer_pubkey>",
  "permissions": {
    "autoJoin":  true,
    "keyboard":  true,
    "mouse":     true,
    "controller": false,
    "immersion": false
  }
}
→ { "ok": true }
```

### Get permissions

```bash 
GET $BASE/api/friends/permissions?host=<host_pubkey>&friend=<viewer_pubkey>
→ { "status":"accepted", "permissions": { ... } }   // 404 if not found
```

### Convenience: upsert friendship + permissions in one call

```bash 
POST $BASE/api/friends/upsert
{
  "host": "<host_pubkey>",
  "friend": "<viewer_pubkey>",
  "permissions": { ... }   // same shape as above
}
→ { "ok": true }
```

### Debugging helpers

```bash
GET $BASE/api/debug/users
GET $BASE/api/debug/friendships
```

--- 

### How permissions work

- autoJoin: viewer is auto-admitted when requesting to join

- keyboard/mouse/controller: which inputs the viewer may send to host (note controller is stubbed)

- immersion: whether “system” combos (e.g., Alt-Tab) are allowed

Input handling is enforced on the host side; adjust with POST /api/friends/permissions.
