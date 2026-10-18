#!/usr/bin/env bash
# Run kindlepost in Docker on macOS. Safe to re-run: creates .env on first run,
# then (re)builds and starts the container and waits until it's healthy.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v docker >/dev/null; then
  echo "Docker is required: install Docker Desktop (https://www.docker.com/products/docker-desktop/)" >&2
  echo "or OrbStack (brew install --cask orbstack), open it once, then re-run this script." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "==> Starting Docker"
  open -a Docker 2>/dev/null || open -a OrbStack 2>/dev/null || true
  for _ in $(seq 60); do docker info >/dev/null 2>&1 && break; sleep 2; done
  docker info >/dev/null 2>&1 || { echo "Docker didn't start; open it manually and re-run." >&2; exit 1; }
fi

if [ ! -f .env ]; then
  sed "s/^API_TOKEN=.*/API_TOKEN=$(openssl rand -hex 24)/" .env.example > .env
  echo "==> Created .env with a new API_TOKEN"
fi
PORT="$(sed -n 's/^HOST_PORT=//p' .env)"
PORT="${PORT:-8090}"

echo "==> Building and starting (first build downloads Calibre and takes a few minutes)"
docker compose up -d --build

echo "==> Waiting for the server"
for _ in $(seq 30); do
  curl -fsS localhost:$PORT/health 2>/dev/null | grep -q '"ok":true' && break
  sleep 2
done
curl -fsS localhost:$PORT/health | grep -q '"ok":true' \
  || { echo "Server isn't healthy; see: docker compose logs" >&2; exit 1; }

IP="$(ipconfig getifaddr en0 2>/dev/null || true)"
TS="$(tailscale ip -4 2>/dev/null | head -1 || true)"
echo
echo "Running."
echo "  Local:     http://localhost:$PORT/"
[ -n "$IP" ] && echo "  LAN:       http://$IP:$PORT/"
[ -n "$TS" ] && echo "  Tailscale: http://$TS:$PORT/"
echo
echo "Token: docker compose exec kindlepost printenv API_TOKEN"
echo "Logs:  docker compose logs -f    Stop: docker compose down"
