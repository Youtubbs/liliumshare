# -*- mode: ruby -*-
# vi: set ft=ruby :

require 'json'
require 'socket'

ROOT = File.dirname(__FILE__)

def port_free?(host, port)
  s = TCPServer.new(host, port)
  s.close
  true
rescue Errno::EADDRINUSE, Errno::EACCES
  false
end

def load_json!(path)
  JSON.parse(File.read(path))
rescue Errno::ENOENT
  abort "ERROR: network config not found: #{path}\n" \
        "Set LILIUM_NETCFG or create backend/network_config.vagrant.json"
rescue => e
  abort "ERROR: failed to parse JSON at #{path}: #{e}"
end

def require_key!(h, *ks)
  node = h
  ks.each { |k| node = (node.is_a?(Hash) ? node[k] : nil) }
  if node.nil? || node.to_s.strip.empty?
    abort "ERROR: missing required key: #{ks.join('.')}"
  end
  node
end

def int_key!(h, *ks)
  Integer(require_key!(h, *ks))
end

# ---------- Load config ----------
netcfg_path = ENV['LILIUM_NETCFG'] || File.join(ROOT, 'backend', 'network_config.vagrant.json')
cfg = load_json!(netcfg_path)

# DB
db_ip       = require_key!(cfg, 'db', 'ip')
db_port     = int_key!(cfg, 'db', 'port')
db_cport    = int_key!(cfg, 'db', 'container_port')
db_user     = require_key!(cfg, 'db', 'user')
db_pass     = require_key!(cfg, 'db', 'password')
db_name     = require_key!(cfg, 'db', 'database')
db_url      = (cfg.dig('database','url') || "postgres://#{db_user}:#{db_pass}@#{db_ip}:#{db_port}/#{db_name}")

# API
api_ip      = require_key!(cfg, 'api', 'ip')
api_port    = (cfg.dig('api', 'port') || cfg.dig('backend', 'host_http_port'))
api_cport   = (cfg.dig('api', 'container_port') || cfg.dig('backend', 'container_http_port'))
abort "ERROR: api.port/backend.host_http_port missing"                 if api_port.nil?
abort "ERROR: api.container_port/backend.container_http_port missing" if api_cport.nil?
api_port    = Integer(api_port)
api_cport   = Integer(api_cport)

# Edge
edge_ip     = require_key!(cfg, 'edge', 'ip')
edge_port   = int_key!(cfg, 'edge', 'port')

# Host forwarding (may be occupied by VBox NAT; we’ll avoid forwarding if busy)
host_fwd_ip   = (cfg.dig('host', 'forward_ip') || '127.0.0.1')
host_fwd_port = int_key!(cfg, 'host', 'forward_http')

BOX = 'bento/ubuntu-22.04'

Vagrant.configure('2') do |config|
  config.vm.box = BOX
  config.vm.boot_timeout = 600
  config.ssh.insert_key = false

  # Keep the whole repo visible to VMs (build docker images from /vagrant)
  config.vm.synced_folder '.', '/vagrant', type: 'virtualbox'

  # ---------------------------
  # VM: Database (PostgreSQL)
  # ---------------------------
  config.vm.define 'vm-db' do |db|
    db.vm.hostname = 'vm-db'
    db.vm.network 'private_network', ip: db_ip

    db.vm.provider 'virtualbox' do |vb|
      vb.cpus = 1
      vb.memory = 1024
    end

    db.vm.provision 'shell', inline: <<~SHELL
      set -Eeuo pipefail
      export DEBIAN_FRONTEND=noninteractive

      apt-get update -y
      apt-get install -y --no-install-recommends docker.io ca-certificates curl
      systemctl enable --now docker

      docker rm -f lilium-postgres >/dev/null 2>&1 || true
      docker volume create lilium-pgdata >/dev/null

      docker run -d --name lilium-postgres \
        -e POSTGRES_USER=#{db_user} \
        -e POSTGRES_PASSWORD=#{db_pass} \
        -e POSTGRES_DB=#{db_name} \
        -v lilium-pgdata:/var/lib/postgresql/data \
        -p #{db_port}:#{db_cport} \
        --health-cmd="pg_isready -U #{db_user} -d #{db_name} -h 127.0.0.1 -p #{db_cport}" \
        --health-interval=5s --health-timeout=5s --health-retries=12 \
        postgres:16
    SHELL
  end

  # ---------------------------
  # VM: API (Node backend)
  # ---------------------------
  config.vm.define 'vm-api' do |api|
    api.vm.hostname = 'vm-api'
    api.vm.network 'private_network', ip: api_ip

    api.vm.provider 'virtualbox' do |vb|
      vb.cpus = 2
      vb.memory = 1024
    end

    api.vm.provision 'shell', inline: <<~SHELL
      set -Eeuo pipefail
      export DEBIAN_FRONTEND=noninteractive

      apt-get update -y
      apt-get install -y --no-install-recommends docker.io ca-certificates curl postgresql-client netcat-openbsd
      systemctl enable --now docker

      echo "Waiting for DB at #{db_ip}:#{db_port} ..."
      for i in $(seq 1 90); do
        if pg_isready -h #{db_ip} -p #{db_port} -U #{db_user} -d #{db_name} >/dev/null 2>&1; then
          echo "DB is ready (pg_isready)."
          break
        fi
        if nc -z -w1 #{db_ip} #{db_port} >/dev/null 2>&1; then
          echo "DB TCP port is open."
          break
        fi
        sleep 2
      done

      if ! pg_isready -h #{db_ip} -p #{db_port} -U #{db_user} -d #{db_name} >/dev/null 2>&1; then
        echo "ERROR: Postgres at #{db_ip}:#{db_port} still not reachable after waiting." >&2
        echo "Tip: vagrant ssh vm-db -c 'docker ps; docker logs --tail=200 lilium-postgres'" >&2
        exit 1
      fi

      cd /vagrant
      docker build -t lilium-backend ./backend

      # Run migration once, with the same netcfg the server will read
      set +e
      docker run --rm \
        -e DATABASE_URL='postgres://#{db_user}:#{db_pass}@#{db_ip}:#{db_port}/#{db_name}' \
        -e LILIUM_NETCFG=/app/network_config.json \
        -v /vagrant/backend/network_config.vagrant.json:/app/network_config.json:ro \
        lilium-backend node src/migrate.js
      MIGRC=$?
      set -e
      if [ "$MIGRC" -ne 0 ]; then
        echo "ERROR: DB migration failed ($MIGRC)." >&2
        exit "$MIGRC"
      fi

      # Start API container with JSON-driven ports
      docker rm -f lilium-backend >/dev/null 2>&1 || true
      docker run -d --name lilium-backend --restart=unless-stopped \
        -e PORT=#{api_cport} \
        -e DATABASE_URL='postgres://#{db_user}:#{db_pass}@#{db_ip}:#{db_port}/#{db_name}' \
        -e LILIUM_NETCFG=/app/network_config.json \
        -p #{api_port}:#{api_cport} \
        -v /vagrant/backend/network_config.vagrant.json:/app/network_config.json:ro \
        lilium-backend node src/server.js

      echo "API container started."
      docker ps --filter name=lilium-backend
    SHELL
  end

  # ---------------------------
  # VM: Edge (nginx reverse proxy)
  # ---------------------------
  config.vm.define 'vm-edge' do |edge|
    edge.vm.hostname = 'vm-edge'
    edge.vm.network 'private_network', ip: edge_ip

    # Only forward if host port is actually free (often VBox NAT owns 18080)
    if port_free?('127.0.0.1', host_fwd_port)
      edge.vm.network 'forwarded_port',
        guest: edge_port,
        host:  host_fwd_port,
        auto_correct: true
    else
      puts "NOTE: Host port #{host_fwd_port} is busy; skipping NAT forward. " \
           "Use http://#{edge_ip}:#{edge_port} from the host."
    end

    edge.vm.provider 'virtualbox' do |vb|
      vb.cpus   = 1
      vb.memory = 1024
      vb.gui    = false
    end

    edge.vm.boot_timeout = 900

    edge.vm.provision 'shell', inline: <<~SHELL
      set -Eeuo pipefail
      export DEBIAN_FRONTEND=noninteractive
      apt-get update
      apt-get install -y nginx

      cat >/etc/nginx/sites-available/lilium <<'NGINX'
server {
  listen #{edge_port};
  server_name _;

  proxy_set_header Host $host;
  proxy_set_header X-Real-IP $remote_addr;
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Proto $scheme;

  location / {
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_pass http://#{api_ip}:#{api_port};
  }

  location /ws {
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_pass http://#{api_ip}:#{api_port}/ws;
  }
}
NGINX

      rm -f /etc/nginx/sites-enabled/default
      ln -sf /etc/nginx/sites-available/lilium /etc/nginx/sites-enabled/lilium
      nginx -t
      systemctl restart nginx
    SHELL
  end

  forward_note =
    if port_free?('127.0.0.1', host_fwd_port)
      "Host → Edge: http://#{host_fwd_ip}:#{host_fwd_port}"
    else
      "Host-only IP → Edge: http://#{edge_ip}:#{edge_port}"
    end

  config.vm.post_up_message = <<~MSG
    Loaded network config: #{netcfg_path}

    DB VM:   #{db_ip}:#{db_port}  (container: #{db_cport}, user: #{db_user}, db: #{db_name})
    API VM:  #{api_ip}:#{api_port} (container: #{api_cport})
    EDGE VM: #{edge_ip}:#{edge_port}

    #{forward_note}

    Try:
      curl -fsS #{port_free?('127.0.0.1', host_fwd_port) ? "http://#{host_fwd_ip}:#{host_fwd_port}" : "http://#{edge_ip}:#{edge_port}"} /health
  MSG
end
