#!/usr/bin/env bash
set -euo pipefail

HOST="browser-45-86-180-49.sslip.io"
SITE="/etc/nginx/sites-available/chatgpt-browser-mcp"
ACME_ROOT="/var/www/chatgpt-browser-acme"
SSL_DIR="/etc/nginx/ssl/chatgpt-browser"

command -v nginx >/dev/null 2>&1
command -v openssl >/dev/null 2>&1

mkdir -p "$ACME_ROOT/.well-known/acme-challenge" "$SSL_DIR"
install -m 0644 /opt/chatgpt-browser/nginx-bootstrap.conf "$SITE"
ln -sfn "$SITE" /etc/nginx/sites-enabled/chatgpt-browser-mcp
nginx -t
systemctl reload nginx

if [ ! -x /root/.acme.sh/acme.sh ]; then
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL https://get.acme.sh | sh -s email=shop@office-mag.com
  else
    wget -qO- https://get.acme.sh | sh -s email=shop@office-mag.com
  fi
fi

/root/.acme.sh/acme.sh --set-default-ca --server letsencrypt
if [ ! -s "$SSL_DIR/fullchain.pem" ] || [ ! -s "$SSL_DIR/key.pem" ]; then
  /root/.acme.sh/acme.sh --issue --server letsencrypt -d "$HOST" -w "$ACME_ROOT" --keylength ec-256
fi
touch "$SSL_DIR/key.pem" "$SSL_DIR/fullchain.pem"
/root/.acme.sh/acme.sh --install-cert -d "$HOST" --ecc   --key-file "$SSL_DIR/key.pem"   --fullchain-file "$SSL_DIR/fullchain.pem"   --reloadcmd "systemctl reload nginx"

chmod 600 "$SSL_DIR/key.pem"
chmod 644 "$SSL_DIR/fullchain.pem"
install -m 0644 /opt/chatgpt-browser/nginx.conf "$SITE"
nginx -t
systemctl reload nginx

curl -fsS --max-time 10 "http://127.0.0.1:18000/health"
