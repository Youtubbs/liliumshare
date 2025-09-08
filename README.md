# LiliumShare — local virtualised prototype

A minimal Parsec-style screen sharing prototype with input permissions and audio passthrough, packaged to demonstrate **portable build & deployment** across three VMs.

- **VM layout**
  - `vm-db`  -> PostgreSQL 16 (Docker in-VM) + persistent volume
  - `vm-api` -> Node.js backend (Docker in-VM) + schema migration
  - `vm-edge`-> nginx reverse proxy (VM entrypoint)
- **Frontend (host)**: Python GUI/CLI (WebRTC), runs on your host OS using a venv
- **Config**: All IPs/ports are read from `backend/network_config.vagrant.json` (or `LILIUM_NETCFG`)

---

## Table of contents

- [Prerequisites](#prerequisites)
- [Quick start (one command)](#quick-start-one-command)
- [Health check & baseline test](#health-check--baseline-test)
- [Developing on this project](#developing-on-this-project)
- [What gets downloaded (volumes)](#what-gets-downloaded-volumes)
- [Debugging & troubleshooting](#debugging--troubleshooting)
- [Clean up](#clean-up)
- [Repository layout](#repository-layout)
- [Attribution](#attribution)

---

## Prerequisites

- Linux host (tested on Ubuntu with VirtualBox)
- **VirtualBox** 7.x and **Vagrant** 2.4+
- Python **3.10+**, `venv`, and `pip` on the host (for the GUI)
- You **do not** need Docker on the host. each VM installs what it needs

**VirtualBox host-only network note**  
This project uses the default `192.168.56.0/21` host-only range. If VirtualBox has a different allowed range, edit network settings accordingly.

---

## Quick start (one command)

```bash
# from the repo root
sudo chmod +x build_project.sh 
./build_project.sh --vagrant-up --bootstrap-demo
```

This script:
- Brings all VMs up (vagrant up) and provisions them
- Builds the backend Docker image on vm-api
- Starts Postgres on vm-db, applies schema, starts the backend, configures nginx
- Prints the correct base URL (handles the port-forward “busy” case)
- Bootstraps 3 sample users to play around with. Run the commands given after running the script to activate each users' GUI.

You can also rebuild the entire vagrant setup (deleting VMs included) by running 'scripts/rebuild_from_scratch_vagrant.sh --no-cache' after adding executible permissions to it.

---

## Health check & baseline test

The rebuild script prints the effective base URL. There are two possibilities:

- NAT forward is free -> http://127.0.0.1:18080
- Host port was busy -> forward is skipped; use host-only IP from JSON, e.g. http://192.168.56.30:18080

Verify with:
```bash 
BASE="http://127.0.0.1:18080" # or "http://192.168.56.30:18080"
curl -fsS "$BASE/health" # -> ok true
```

---

## Developing on this project

- **Back end changes (Node/Express, migrations):**
  - Edit files under backend/src (e.g., add a new endpoint or tweak schema in migrate.js).
  - Rebuild & restart the back end:
    ```bash
    scripts/rebuild_from_scratch_vagrant.sh --no-cache
    # or faster (without DB/container reset):
    vagrant ssh vm-api -c 'cd /vagrant && docker build -t lilium-backend ./backend && docker rm -f lilium-backend || true && docker run -d --name lilium-backend -p <vm_port>:<ct_port> ... lilium-backend node src/server.js'
    ```
  - Migrations are idempotent. Updates to migrate.js are safe to reapply.
- **GUI changes (Python):**
  - Edit files under frontend/.
  - Make sure your venv is active: source venv/bin/activate
  - Re-run the appropriate HOME=... python frontend/gui.py process(es).
- **Non-Vagrant local dev:**
  - There’s a docker-compose.yml for local (single-host) development. Look to previous versions of this project if you're interested in doing single host development; I abandoned it for the sake of completing this assignment for my class.

---

## What gets downloaded (volumes)

- First-time APT per VM (Docker/containers/nginx): ~80–100 MB
- Docker base images (once, cached in the VM): postgres:16, node:20-slim (hundreds of MB)
- On-disk after a complete build (including bootstrapped users): ~494.6 MB under the repo; total VMs ~11.9 GB
  - vm-api ~4.1 GB, vm-db ~4.3 GB, vm-edge ~3.5 GB
- Subsequent redeploys: negligible changes (a few megabytes maybe; only changes in my experience come from changed app layers + any OS updates)

**Resources (per VM):**
- vm-db 1 vCPU / 1024 MiB
- vm-api 2 vCPU / 1024 MiB
- vm-edge 1 vCPU / 1024 MiB

Feel free to change these as you see fit in the Vagrantfile; just make sure to edit this README if you do.

---

## Debugging & troubleshooting

**Find the right BASE URL**
- If you see 'NOTE: Host port 18080 is busy; skipping NAT forward', use the host-only URL printed by Vagrant, e.g. http://192.168.56.30:18080.
- Otherwise use http://127.0.0.1:18080.
**502 Bad Gateway from nginx**
- You likely hit 127.0.0.1:18080 when NAT forwarding was skipped.
- Fix: use the host-only URL: http://<edge.ip>:<edge.port> from backend/network_config.vagrant.json.
**Check service health**
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
# API container logs
vagrant ssh vm-api -c "docker logs -f lilium-backend"

# Postgres container logs
vagrant ssh vm-db -c "docker logs -f lilium-postgres"

# nginx access/error
vagrant ssh vm-edge -c "sudo tail -f /var/log/nginx/access.log /var/log/nginx/error.log"
```
**Reprovision a single VM**
```bash
vagrant provision vm-* # Replace * with either db, api, or edge as needed
```
**Networking ranges error (VirtualBox)**

If you see “IP address ... not within the allowed ranges”, adjust /etc/vbox/networks.conf to include 192.168.56.0/21.

**“Guest Additions version mismatch” warning**

This is harmless for this project. Shared folders and networking still work; continue.

**Screen sharing window not popping up**

Run 'scripts/headless_sanitycheck.sh' after giving execution priviledges. If your environment isn't 'Wayland' or 'X11', this project probably won't work for you without some modifications on your part. 

**OpenCV errors**

Run 'scripts/openCV_sanitycheck.sh' after giving execution priviledges. You should see a green square pop up. If not, look up tutorials on how to fix your openCV install. 

---

## Clean up

```bash 
# Stop VMs
vagrant halt

# Destroy VMs and their virtual disks
vagrant destroy -f

# Remove generated demo users and the venv you created
rm -rf _userA_home _userB_home _userC_home venv
```

---

## Repository Layout

```bash
├── backend
│   ├── Dockerfile
│   ├── network_config.json
│   ├── network_config.vagrant.json
│   ├── package.json
│   └── src
│       ├── db.js
│       ├── migrate.js
│       └── server.js
├── build_project.sh
├── docker-compose.yml
├── frontend
│   ├── audio_capture.py
│   ├── client.py
│   ├── gui.py
│   ├── gui_complex.py
│   ├── input_inject.py
│   ├── keys.py
│   ├── portal_capture.py
│   ├── requirements.txt
│   ├── rtc_host.py
│   ├── rtc_viewer.py
│   ├── screen_capture.py
│   └── signaling.py
├── scripts
│   ├── bootstrap_local_triple_user.py
│   ├── headless_sanitycheck.sh
│   ├── openCV_sanitycheck.sh
│   └── rebuild_from_scratch_vagrant.sh
└── Vagrantfile
```

---

## Attribution

This project uses open-source software including VirtualBox, Vagrant, Ubuntu, Docker, PostgreSQL, nginx, Node.js, and Python libraries (requests, cryptography, and the packages listed in frontend/requirements.txt). I used ChatGPT to help me figure out how to use certain packages, how to troubleshoot my code, and write comments (including some for this README).