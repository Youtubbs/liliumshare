# LiliumShare

A minimal Parsec-style screen sharing prototype with input permissions and audio passthrough. It demonstrates **portable build & deployment** either on a **local, three-VM Vagrant lab** or on **AWS (ECS Fargate + ALB + Secrets Manager + Postgres)**, with a **Python GUI** for end users.

Note: This is more of a hobby project for me and a way for me to learn how to use AWS. I don't recommend hosting the service in its current state unless it's for development purposes! 

> Prototype status: Suitable for demos. Currently, end-to-end chat communication works but only via local communication. Video + audio packets are shared between sessions but are invalid for some odd reason? See the logs in the local demo for proof of this.

> I believe you could set up port forwarding to a server that hosts the 3 VMs and host the service with minimal modification, but I don't have the ability to test this. 

> If you're interested in hosting on AWS, it seems that health checks are working, but the actual DB isn't accepting data from the ALB for some reason. More testing is needed. I can tell that AWS implementation is close to working, though. 

---

## Table of Contents

* [Quick Start — End-User GUI (Host & Viewer)](#quick-start--end-user-gui-host--viewer)
* [AWS deployment](#aws-deployment)

  * [What it creates](#what-it-creates)
  * [Prerequisites](#prerequisites-aws)
  * [Deploy](#deploy)
  * [Test](#test)
  * [Cost control (stop or tear down)](#cost-control-stop-or-tear-down)
  * [Troubleshooting AWS](#troubleshooting-aws)
* [Local (Vagrant) build](#local-vagrant-build)

  * [One-command build](#one-command-build)
  * [Health check](#health-check)
  * [Debugging & logs](#debugging--logs)
* [Architecture diagrams](#architecture-diagrams)
* [Configuration](#configuration)
* [What gets downloaded (volumes & sizes)](#what-gets-downloaded-volumes--sizes)
* [Repository layout](#repository-layout)
* [Attribution](#attribution)

---

## Quick Start — End-User GUI (Host & Viewer)

These steps assume either:
- **Local mode:** you ran the Vagrant build (see “Local (Vagrant) build”); or  
- **AWS mode:** you deployed the backend behind an ALB (see “AWS deployment”).

### 0) Prereqs (host machine)
- Python **3.10+**, `venv`, and `pip`.
- Linux desktop (tested on Debian/Ubuntu Wayland). 
  - Should work on X11 as well. 
  - I don't see why the GUI wouldn't work on Windows and Mac too, but I'm not able to test this due to my device being unable to handle expensive VMs like these. 
- If you’ll run locally with Vagrant: **VirtualBox 7.x** and **Vagrant 2.4+**.

### 1) Create and activate a venv, install GUI deps
```bash
# from repo root
python3 -m venv venv
source venv/bin/activate
pip install -r frontend/requirements.txt
````

### 2) Point the GUI at your backend

The GUI reads `backend/network_config.json` (or `LILIUM_NETCFG`) for `backend.http_base` and `backend.ws_base`.

* **Local (Vagrant)**: `backend/network_config.vagrant.json` already points at the Edge VM.
* **AWS**: edit `backend/network_config.json` with your ALB DNS:

```json
{
  "backend": {
    "http_base": "http://<your-alb-dns>",
    "ws_base":  "ws://<your-alb-dns>/ws",
    "port": 80
  }
}
```

Then, either set:

```bash
export LILIUM_NETCFG="$PWD/backend/network_config.json"   # AWS
# or
export LILIUM_NETCFG="$PWD/backend/network_config.vagrant.json"  # Vagrant
```

### 3) Launch the GUI (as Host and as Viewer)

Open two terminals (or two machines). In each, activate the venv.


```bash
HOME="$PWD/_user_key_home" python3 frontend/gui.py
```

### 4) First-time setup in the GUI

* Each user is provisioned with a keypair and optional nickname (Found in whatever directory you point to with 'generate keys' and 'load keys'). 
* In the Viewer GUI, search the Host by nickname (optional) and their pubkey (required).
* Send a **friend request**; the Host accepts on their side.
  (You can also pre-approve friendships via the REST API if desired for demo purposes. See scripts/bootstrap_local_triple_user.py.)

### 5) Start a session

Screen-sharing (Non-functional currently; temp screensaver to show successful packet transfer):

* Host clicks **Share my screen** with their choice of audio/video source.
* Viewer clicks **Join** on the Host.
* The viewer sees a screensaver which demonstrates audio/video packet transfer between users.
* The Host can toggle **input permissions** (keyboard/mouse/controller) and **immersion** per-user.

Messaging (Functional):

* Host initiates chat by clicking **Message** while highlighting the desired friend. 
* The friend clicks **message** while highlighting the host's name. 
* The friend and host engage in an end-to-end encrypted chat that disappears when the chat is over with.

### 6) Optional: built-in bootstrap (local demo data)

If you used the local build script with `--bootstrap-demo`, you already have three demo users (`HostA`, `ViewerB`, `UserC`). Run the GUIs exactly as the script prints on completion, for ready-to-test sessions.

> Troubleshooting GUI:
>
> * `scripts/openCV_sanitycheck.sh` should open a green window for ~5s.
> * `scripts/headless_sanitycheck.sh` prints Wayland/X11 environment details.

---

## AWS deployment

### What it creates

* **ECR** repo for the backend image.
* **ECS (Fargate)** cluster + service (1 task by default).
* **Application Load Balancer** (HTTP on port 80) with `/health` checks.
* **CloudWatch Logs** group `/ecs/liliumshare`.
* **One-off migration task** that runs DB schema (via Secrets Manager DB URL).
* Uses an existing **Secrets Manager** secret for `DATABASE_URL`.

> Database: the app expects `DATABASE_URL` to be a Postgres connection string (e.g. RDS or externally hosted Postgres). The migration task runs against that URL.

### Prerequisites (AWS)

* AWS CLI configured with privileges to use **ECR**, **ECS**, **ELBv2**, **Logs**, **Secrets Manager**, and **IAM**.
* Docker and `jq` installed locally.
* A **Secret** in Secrets Manager named (default):

  * `liliumshare/prod/DATABASE_URL` with a value like
    `postgres://USERNAME:PASSWORD@HOST:5432/liliumshare`
* A **default VPC** and at least two subnets in the target region (e.g. `ap-southeast-2`).

### Deploy

```bash
# from repo root
chmod +x build_project_aws.sh
./build_project_aws.sh
```

The script will:

* Build + push the backend image to ECR.
* Register a migrate Task Definition and run the migration one-off.
* Create/update the runtime Task Definition and ECS Service (Fargate).
* Create/attach the Target Group and ALB listener.
* Print the **ALB URL** at the end.

> If you prefer HTTPS, attach a certificate to the ALB listener (not included here).

### Test

```bash
ALB="http://<your-alb-dns>"
curl -fsS "$ALB/health"             # -> {"ok":true}
curl -fsS "$ALB/api/debug/users"    # -> [...]
```

Update your GUI config to point to the ALB (see the **GUI** quick start).

### Cost control (stop or tear down)

Two modes are provided:

```bash
chmod +x scripts/turn_off_service.sh

# 1) Stop compute (leave ALB — you still pay small hourly ALB cost)
scripts/turn_off_service.sh --turn-off

# 2) Minimal cost (scale ECS to 0 AND delete ALB/TG)
scripts/turn_off_service.sh --minimal-cost
```

Re-deploy later with `./build_project_aws.sh`.

### Troubleshooting AWS

* Deep diagnostics:

  ```bash
  chmod +x scripts/aws_test.sh
  scripts/aws_test.sh
  ```

  Prints ALB/TG health, ECS service status, task ENI, SG rules, subnet routes/NACLs, and recent logs.

* Health probe passes but API calls hang: check DB reachability from the task; verify the **ECS SG -> DB SG** rules permit TCP/5432 and the DB is in the same VPC or reachable through routing.

* Secrets: ensure `liliumshare/prod/DATABASE_URL` exists in the same region and is a full Postgres URL.

---

## Local (Vagrant) build

### One-command build

```bash
# from repo root
chmod +x build_project.sh 
./build_project.sh --vagrant-up --bootstrap-demo
```

This:

* Brings up the three VMs and provisions them.
* Builds the backend image on `vm-api`; starts Postgres on `vm-db`; configures nginx on `vm-edge`.
* Detects an available base URL and prints it.
* Optionally bootstraps three demo users (A,B,C) and prints per-user GUI commands.

> Full reset:
>
> ```bash
> chmod +x scripts/rebuild_from_scratch_vagrant.sh
> scripts/rebuild_from_scratch_vagrant.sh --no-cache
> ```

> Full deletion of VMs (no warning):
>
> ```bash
> vagrant destroy -f
> ```

### Health check

Two possibilities:

* NAT forward free -> `http://127.0.0.1:18080`
* Host port busy -> use host-only IP from JSON, e.g. `http://192.168.56.30:18080`

```bash
BASE="http://127.0.0.1:18080" # or "http://192.168.56.30:18080"
curl -fsS "$BASE/health"  # -> {"ok":true}
```

### Debugging & logs

**Find the right BASE URL**

* If you see “Host port 18080 is busy; skipping NAT forward”, use the host-only URL.

**502 from nginx**

* You probably hit localhost when NAT forwarding was skipped. Use the host-only URL from `backend/network_config.vagrant.json`.

**Quick probes**

```bash
# Edge -> API
curl -v "$BASE/health"

# From vm-edge -> vm-api
vagrant ssh vm-edge -c "curl -v http://$(jq -r .api.ip backend/network_config.vagrant.json):$(jq -r .api.port backend/network_config.vagrant.json)/health"

# From vm-api -> DB port
vagrant ssh vm-api -c "nc -vz $(jq -r .db.ip backend/network_config.vagrant.json) $(jq -r .db.port backend/network_config.vagrant.json)"
```

**Logs**

```bash
vagrant ssh vm-api -c "docker logs -f lilium-backend"      # API container logs
vagrant ssh vm-db  -c "docker logs -f lilium-postgres"     # Postgres
vagrant ssh vm-edge -c "sudo tail -f /var/log/nginx/access.log /var/log/nginx/error.log"
```

**Reprovision a single VM**

```bash
vagrant provision vm-db|vm-api|vm-edge
```

**VirtualBox range error**

* Add `192.168.56.0/21` to `/etc/vbox/networks.conf`.

**GUI / OpenCV sanity**

```bash
scripts/openCV_sanitycheck.sh
scripts/headless_sanitycheck.sh
```

---

## Architecture diagrams

### Cloud service Layout:

```mermaid
flowchart LR
 subgraph ECS["ECS Cluster (Fargate) — liliumshare-cluster"]
    direction TB
        SVC["ECS Service — liliumshare-svc<br>Desired: 0–1 tasks"]
        TASK["Task: liliumshare-backend<br>ContainerPort: 18080<br>Image: :latest (ECR)<br>Logs: CloudWatch /ecs/liliumshare"]
  end
    SVC --> TASK
    UHost["Host GUI (Python)<br>End user"] -- HTTP :80<br>WS /ws --> ALB["Application Load Balancer<br>Listener :80<br>HealthCheck: GET /health"]
    UView["Viewer GUI (Python)<br>End user"] -- HTTP :80<br>WS /ws --> ALB
    ALB -- Forward --> TG["Target Group (ip)<br>Port 18080<br>Matcher: 200-399"]
    TG -- IP targets --> TASK
    TASK -- GET /health --> TASK
    TASK -- Outbound :5432<br>PG driver --> DB[("PostgreSQL<br>RDS or external<br>postgres://…:5432/liliumshare")]
    ECR["ECR repo<br>liliumshare-backend"] -- Pull image --> TASK
    TASK -- stdout/stderr --> CWL["CloudWatch Logs<br>/ecs/liliumshare"]
    SM["Secrets Manager<br>liliumshare/prod/DATABASE_URL"] -- Inject env: DATABASE_URL --> TASK
    ALBSG["ALB SG:<br>allow 0.0.0.0/0 on :80"] -. protects .-> ALB
    ECSSG["ECS SG:<br>allow :18080 from ALB SG"] -. protects .-> TASK
    DBSG["DB SG:<br>allow :5432 from ECS SG"] -. protects .-> DB
    TASK_NOTES["Express (HTTP) + WS (/ws)<br>Routes:<br>/health<br>/api/register<br>/api/friends/*<br>/api/debug/*<br>WS relays:<br>offer/answer/ice<br>chat-hello/chat-msg<br>input-permissions"] -. details .-> TASK

     ALBSG:::sg
     ECSSG:::sg
     DBSG:::sg
    classDef sg fill:#fff,stroke:#bbb,color:#333,stroke-dasharray: 5 3,font-size:11px
```
    
### Local Demo Layout:

```mermaid
flowchart LR
 subgraph HOST["Host machine (Linux desktop)"]
    direction TB
        GUIA["Host GUI (Python)<br>HOME=_userA_home"]
        GUIB["Viewer GUI (Python)<br>HOME=_userB_home"]
        NAT["Optional NAT forward<br>127.0.0.1:18080 → vm-edge:18080"]
  end
 subgraph VMEDGE["vm-edge 192.168.56.30"]
        NGINX["nginx reverse proxy<br>listen :18080<br>location / → vm-api:18080<br>location /ws → vm-api:18080/ws"]
  end
 subgraph VMAPI["vm-api 192.168.56.20"]
        API["Docker container: lilium-backend<br>PORT 18080<br>LILIUM_NETCFG=/app/network_config.json"]
  end
 subgraph VMDB["vm-db 192.168.56.10"]
        PG["Docker: postgres:16<br>Port :5432<br>Volume: lilium-pgdata"]
  end
 subgraph LAN["VirtualBox Host-Only Network 192.168.56.0/21"]
    direction LR
        VMEDGE
        VMAPI
        VMDB
  end
    GUIA -- "HTTP/WS<br> 192.168.56.30:18080 <br> or 127.0.0.1:18080 (if forwarded)" --> NGINX
    GUIB -- "HTTP/WS<br> 192.168.56.30:18080" --> NGINX
    NAT -. Only if host port free .-> NGINX
    NGINX -- proxy_pass / --> API
    NGINX -- proxy_pass /ws upgrade --> API
    API -- "postgres://lilium:lilium@192.168.56.10:5432/liliumshare" --> PG
    CFGV["backend/network_config.vagrant.json<br>- db/api/edge IPs &amp; ports<br>- backend.http_base/ws_base<br>- database.url"] -. "mounted read-only" .-> API
    API -.-> NOTES["Express REST + WS:<br>- /health<br>- /api/register<br>- /api/friends/*<br>- /api/debug/*<br>On start:<br>- migrate.js runs once (via scripts)<br>- server.js listens on :18080"]
```


### Messaging Interaction Sequence Diagram:

```mermaid
sequenceDiagram
  autonumber
  %% ===== Messaging only (end-to-end, WS-relayed; DB backs relationships/keys) =====
  participant Host as "Host GUI (Python)"
  participant View as "Viewer GUI (Python)"
  participant BE as "Backend (Express + WS)"
  participant DB as "PostgreSQL"

  Note over Host,View: Each GUI creates RSA keypair and nickname

  Host->>BE: POST /api/register (pubkey=HOST_PUB, nickname=HostA)
  BE->>DB: INSERT users ON CONFLICT UPDATE
  BE-->>Host: {ok:true}

  View->>BE: POST /api/register (pubkey=VIEW_PUB, nickname=ViewerB)
  BE->>DB: INSERT users ON CONFLICT UPDATE
  BE-->>View: {ok:true}

  Note over View,BE: Create relationship (pending request)
  View->>BE: POST /api/friends/request (me=VIEW_PUB, friend=HOST_PUB)
  BE->>DB: INSERT friendships(host=HOST_PUB, friend=VIEW_PUB, status=pending)
  BE-->>View: {ok:true}

  Note over Host,BE: Host accepts (or bootstrap uses /api/friends/upsert)
  Host->>BE: POST /api/friends/accept (me=HOST_PUB, friend=VIEW_PUB)
  BE->>DB: UPSERT friendships(..., status=accepted) both directions
  BE-->>Host: {ok:true}

  Note over Host,BE: Optional per-pair connection key for chat
  Host->>BE: POST /api/friends/connkey/generate (host=HOST_PUB, friend=VIEW_PUB)
  BE->>DB: UPSERT connkeys(host,friend,conn_key)
  BE-->>Host: {ok:true, conn_key=<base64>}

  Note over View,BE: Viewer fetches same conn_key
  View->>BE: GET /api/friends/connkey?host=HOST_PUB&friend=VIEW_PUB
  BE->>DB: SELECT conn_key
  BE-->>View: {conn_key:<base64>}

  Note over Host,BE: WebSocket control plane
  Host->>BE: WS CONNECT /ws?pubkey=HOST_PUB
  BE-->>Host: type=hello, you=HOST_PUB
  View->>BE: WS CONNECT /ws?pubkey=VIEW_PUB
  BE-->>View: type=hello, you=VIEW_PUB

  Note over Host,View: E2E chat handshake ciphertext; server opaque Host->>BE: WS type=chat-hello, to=VIEW_PUB, payload=<ciphertext>
  BE-->>View: relay same bytes
  View->>BE: WS type=chat-ack, to=HOST_PUB, payload=<ciphertext>
  BE-->>Host: relay

  par Encrypted chat messages (either direction)
    Host->>BE: WS type=chat-msg, to=VIEW_PUB, payload=<ciphertext>
    BE-->>View: relay
  and
    View->>BE: WS type=chat-msg, to=HOST_PUB, payload=<ciphertext>
    BE-->>Host: relay
  end

  Note over Host,View: Disconnect. BE removes pubkey from in-memory clients map
```  

### Sample Video/Audio Sharing Sequence Diagram:

```mermaid
sequenceDiagram
  autonumber
  participant Host as "Host GUI (Python)"
  participant View as "Viewer GUI (Python)"
  participant BE as "Backend (Express + WS)"
  participant DB as "PostgreSQL"

  Note over Host,View: Prereq — both users registered and friendship accepted
  Host->>BE: POST /api/register (pubkey=HOST_PUB, nickname=HostA)
  View->>BE: POST /api/register (pubkey=VIEW_PUB, nickname=ViewerB)
  Host->>BE: POST /api/friends/accept (me=HOST_PUB, friend=VIEW_PUB)
  BE->>DB: UPSERT friendships status=accepted (both directions)
  BE-->>Host: {ok:true}

  Note over Host,BE: WebSocket control plane connects
  Host->>BE: WS CONNECT /ws?pubkey=HOST_PUB
  BE-->>Host: type=hello, you=HOST_PUB
  View->>BE: WS CONNECT /ws?pubkey=VIEW_PUB
  BE-->>View: type=hello, you=VIEW_PUB

  Note over View,BE: Viewer requests to join the Host session
  View->>BE: WS type=join-request, host=HOST_PUB, viewer=VIEW_PUB
  BE->>DB: SELECT status, permissions FROM friendships WHERE host=HOST_PUB AND friend=VIEW_PUB
  alt accepted and host online
    BE-->>Host: WS type=incoming-join, viewer=VIEW_PUB, permissions=keyboard|mouse|controller|immersion
  else not accepted or host offline
    BE-->>View: WS type=join-denied, reason=not-friends|host-offline
  end

  Note over Host,View: WebRTC signaling via WS (server relays only)
  Host->>BE: WS type=offer, to=VIEW_PUB, sdp=<host SDP>
  BE-->>View: relay offer
  View->>BE: WS type=answer, to=HOST_PUB, sdp=<viewer SDP>
  BE-->>Host: relay answer

  par ICE candidates
    Host->>BE: WS type=ice, to=VIEW_PUB, cand=<candidate>
    BE-->>View: relay
  and
    View->>BE: WS type=ice, to=HOST_PUB, cand=<candidate>
    BE-->>Host: relay
  end

  Note over Host,View: After DTLS SRTP established, media flows peer to peer
  Host->>Host: Start screen_capture.py and audio_capture.py
  Host-->>View: WebRTC SRTP streams (video and audio)
  Host-->>View: WebRTC DataChannel (control and telemetry)

  Note over Host,BE: Live permission updates (affect viewer input)
  Host->>BE: WS type=input-permissions, to=VIEW_PUB, permissions=keyboard:true,mouse:true,controller:false,immersion:false
  BE-->>View: relay and apply locally

  Note over Host,View: Session ends when WS or peer connection closes
```

---

## Configuration

LiliumShare reads network/database settings from JSON and environment variables:

* **Local lab**: `backend/network_config.vagrant.json` (used by Vagrant and helper scripts).
* **AWS / single host**: `backend/network_config.json` (consumed by GUI bootstrap and optional compose).

Back-end database selection:

* `DATABASE_URL` (env/secret) **wins**.
* Else `NETCFG.database.url` (from `LILIUM_NETCFG` file).
* Else `DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME` parts (env).

> The ECS deploy uses **Secrets Manager** to inject `DATABASE_URL` into the task; migrations and runtime both use it.

---

## What gets downloaded in local demo (volumes & sizes):

* First-time APT per VM (Docker/nginx): ~80–100 MB
* Docker base images (cached in VM): `postgres:16`, `node:20-slim`
* On-disk after a complete Vagrant build (inc. demo users): ~494.6 MB under repo; total VMs ~11.9 GB

  * vm-api ~4.1 GB, vm-db ~4.3 GB, vm-edge ~3.5 GB
* VM resources (defaults): db 1 vCPU / 1 GiB; api 2 vCPU / 1 GiB; edge 1 vCPU / 1 GiB

Adjust in `Vagrantfile` if needed.

---

## Repository layout

```text
/home/youtubbs/Documents/COSC349/container_temp/liliumshare
├── backend
│   ├── Dockerfile
│   ├── entrypoint.sh
│   ├── network_config.json
│   ├── network_config.vagrant.json
│   ├── package.json
│   └── src
│       ├── db.js
│       ├── migrate.js
│       └── server.js
├── build_project_aws.sh
├── build_project.sh
├── docker-compose.yml
├── frontend
│   ├── audio_capture.py
│   ├── chat_only.py
│   ├── client.py
│   ├── gui_complex.py
│   ├── gui.py
│   ├── input_inject.py
│   ├── keys.py
│   ├── messaging.py
│   ├── portal_capture.py
│   ├── requirements.txt
│   ├── rtc_host.py
│   ├── rtc_viewer.py
│   ├── screen_capture.py
│   └── signaling.py
├── .gitignore
├── package-lock.json
├── README.md
├── scripts
│   ├── aws_test.sh
│   ├── bootstrap_local_triple_user.py
│   ├── headless_sanitycheck.sh
│   ├── openCV_sanitycheck.sh
│   ├── rebuild_from_scratch_vagrant.sh
│   └── turn_off_service.sh
└── Vagrantfile
```

---

## Attribution

This project uses open-source software including VirtualBox, Vagrant, Ubuntu, Docker, PostgreSQL, nginx, Node.js, and Python libraries (packages in `frontend/requirements.txt`). ChatGPT was used to help with package usage, troubleshooting, and documentation. Copilot was used for the same reasons. 