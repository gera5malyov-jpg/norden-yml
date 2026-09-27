#!/usr/bin/env bash
set -euo pipefail

HOST="browser-45-86-180-49.sslip.io"
CONF="/etc/nginx/sites-available/chatgpt-browser-mcp"

if ! command -v nginx >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y nginx
fi

if ! command -v certbot >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y certbot python3-certbot-nginx
fi

install -m 0644 /opt/chatgpt-browser/nginx.conf "$CONF"
ln -sfn "$CONF" /etc/nginx/sites-enabled/chatgpt-browser-mcp

nginx -t
systemctl reload nginx

if [ ! -f "/etc/letsencrypt/live/$HOST/fullchain.pem" ]; then
  certbot --nginx -d "$HOST" --non-interactive --agree-tos --email shop@office-mag.com --redirect
else
  nginx -t
  systemctl reload nginx
fi

curl -fsS --max-time 10 "http://127.0.0.1:18000/health"
