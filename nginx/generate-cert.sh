#!/bin/sh
# Generate self-signed cert for dev use only
CERT_DIR=/etc/nginx/certs
if [ ! -f "$CERT_DIR/selfsigned.crt" ]; then
    mkdir -p "$CERT_DIR"
    openssl req -x509 -nodes -days 3650 \
        -newkey rsa:2048 \
        -keyout "$CERT_DIR/selfsigned.key" \
        -out "$CERT_DIR/selfsigned.crt" \
        -subj "/CN=localhost/O=SMP-Dev"
    echo "Self-signed certificate generated."
else
    echo "Certificate already exists, skipping generation."
fi
