# CC Handoff: Add Nginx HTTP/2 Reverse Proxy to Docker Compose

## Problem

SMP suffers from intermittent 30–50 second page-load stalls caused by HTTP/1.1 head-of-line blocking. The root cause: the browser's SSE EventSource connection occupies an HTTP/1.1 keep-alive connection slot. When idle keep-alive connections expire (uvicorn default ~5s), the browser is left reusing the SSE-occupied connection for the next page navigation. The server can't respond to the new request until the SSE generator releases that connection — which can take 15–50 seconds depending on timing.

This was confirmed via browser automation testing:
- `fetch()` from JavaScript returns in 5–21ms (uses a separate connection)
- Full-page navigation on the *same* connection as SSE stalls 30–50s
- Navigation Timing API shows `stalled_ms: 0`, `tcp_ms: 0`, `requestStart: 20ms` — the request is sent instantly, the server just can't respond on that connection

HTTP/2 fixes this completely via stream multiplexing — SSE, page loads, and API calls all share one TCP connection with no head-of-line blocking.

## Scope

Add an Nginx reverse proxy to the Docker Compose dev stack that terminates HTTP/2 (with TLS via self-signed cert) and proxies to uvicorn over HTTP/1.1 internally. No changes to application code.

## Architecture

```
Browser ──HTTP/2+TLS──▶ Nginx (:8443) ──HTTP/1.1──▶ Uvicorn (:8000)
                          │
                          ├── Serves /static/ directly (optional optimization)
                          └── Proxies everything else to smp:8000
```

## Implementation Steps

### 1. Create `nginx/` directory with config and self-signed cert generator

**File: `nginx/nginx.conf`**

```nginx
worker_processes auto;

events {
    worker_connections 1024;
}

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;

    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;

    # Keep upstream connections alive
    upstream smp_backend {
        server smp:8000;
        keepalive 32;
    }

    server {
        listen 8443 ssl http2;

        ssl_certificate     /etc/nginx/certs/selfsigned.crt;
        ssl_certificate_key /etc/nginx/certs/selfsigned.key;
        ssl_protocols       TLSv1.2 TLSv1.3;
        ssl_ciphers         HIGH:!aNULL:!MD5;

        # SSE endpoints — disable buffering so events stream immediately
        location ~ ^/api/(stream|node-dashboard/stream) {
            proxy_pass http://smp_backend;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;

            # Critical for SSE
            proxy_buffering off;
            proxy_cache off;
            proxy_read_timeout 86400s;
            chunked_transfer_encoding off;
        }

        # Static assets — serve directly if volume-mounted, otherwise proxy
        # For now, proxy everything through uvicorn for simplicity
        location / {
            proxy_pass http://smp_backend;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }
    }
}
```

**File: `nginx/generate-cert.sh`**

```bash
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
```

### 2. Create `nginx/Dockerfile`

```dockerfile
FROM nginx:1.27-alpine

RUN apk add --no-cache openssl

COPY generate-cert.sh /docker-entrypoint.d/10-generate-cert.sh
RUN chmod +x /docker-entrypoint.d/10-generate-cert.sh

COPY nginx.conf /etc/nginx/nginx.conf

EXPOSE 8443
```

Note: Nginx's official Docker image auto-runs scripts in `/docker-entrypoint.d/` on startup, so the cert generation happens automatically on first boot.

### 3. Update `docker-compose.yml`

Add the `nginx` service and adjust port exposure:

```yaml
# DEV ONLY — do not use these credentials in production. Override via .env file.
services:
  nginx:
    build: ./nginx
    ports:
      - "8443:8443"
    depends_on:
      - smp
    volumes:
      - nginx-certs:/etc/nginx/certs

  smp:
    build: .
    # Remove host port mapping — only nginx talks to smp now
    # Keep port 8000 exposed (not published) for internal Docker network access
    expose:
      - "8000"
    cap_add:
      - NET_RAW
    environment:
      - DATABASE_URL=postgresql+psycopg://smp:smp@postgres:5432/smp
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy

  postgres:
    image: docker.io/library/postgres:16-alpine
    environment:
      - POSTGRES_DB=smp
      - POSTGRES_USER=smp
      - POSTGRES_PASSWORD=smp
    volumes:
      - pg-data:/var/lib/postgresql/data
    ports:
      - "5433:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U smp"]
      interval: 2s
      timeout: 3s
      retries: 5

  redis:
    image: docker.io/library/redis:7-alpine
    command: redis-server --save "" --appendonly no
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 2s
      timeout: 3s
      retries: 5

volumes:
  pg-data:
  nginx-certs:
```

Key changes:
- **`nginx` service** added — builds from `./nginx`, publishes port 8443
- **`smp` service** changed from `ports: ["8000:8000"]` to `expose: ["8000"]` — uvicorn is only reachable from inside the Docker network, not from the host directly
- **`nginx-certs` volume** persists the self-signed cert across restarts
- **Redis healthcheck** was truncated in the original file — ensure the closing bracket is present

### 4. Update frontend `apiRequest()` and SSE base URL

The frontend currently connects to SSE and API endpoints using relative URLs (e.g., `/api/stream/events`), so no URL changes are needed — the browser will use the same origin (now `https://localhost:8443`).

**No application code changes required.** The Nginx proxy is transparent.

### 5. Optional: Add direct Nginx static file serving

For a small performance boost, mount the static directory into the Nginx container and serve `/static/` directly without proxying to uvicorn. Add to `docker-compose.yml` under the `nginx` service:

```yaml
  nginx:
    build: ./nginx
    ports:
      - "8443:8443"
    depends_on:
      - smp
    volumes:
      - nginx-certs:/etc/nginx/certs
      - ./static:/app/static:ro  # Direct static serving
```

And update `nginx.conf` to add a location block before the catch-all:

```nginx
        location /static/ {
            alias /app/static/;
            expires 24h;
            add_header Cache-Control "public, no-transform";
        }
```

This is optional — the `StaticCacheMiddleware` already handles caching headers when proxied through uvicorn.

## Verification

1. **Build and start:**
   ```bash
   docker compose down
   docker compose up -d --build
   ```

2. **Verify HTTP/2 is working:**
   ```bash
   curl -k -I --http2 https://localhost:8443/nodes/dashboard
   ```
   Expected: response includes `HTTP/2 200`. The `-k` flag accepts the self-signed cert.

3. **Browser test:**
   - Open `https://localhost:8443` in Edge/Chrome (accept the self-signed cert warning)
   - Open DevTools → Network tab
   - Verify the "Protocol" column shows `h2` for all requests
   - Navigate between pages — should be consistently fast with no 30s stalls
   - Let the page sit idle for 30+ seconds, then navigate — should respond instantly

4. **SSE test:**
   - On any page, verify the SSE connection is active (Network tab → filter by EventStream)
   - The SSE stream should show `h2` protocol
   - Navigate away and back — SSE should reconnect within the backoff window

5. **Run existing tests** (no changes expected since app code is unchanged):
   ```bash
   python -m unittest discover -s tests
   python -m compileall -f app tests alembic
   ```

## Files to Create

| File | Purpose |
|------|---------|
| `nginx/nginx.conf` | Nginx reverse proxy config with HTTP/2, SSE-aware locations |
| `nginx/generate-cert.sh` | Auto-generates self-signed TLS cert on first boot |
| `nginx/Dockerfile` | Builds Nginx image with cert generation |

## Files to Modify

| File | Change |
|------|--------|
| `docker-compose.yml` | Add nginx service, change smp from `ports` to `expose`, add nginx-certs volume, fix redis healthcheck truncation |
| `CHANGELOG.md` | Add entry for HTTP/2 reverse proxy |
| `docs/AGENT_HANDOFF.md` | Add handoff entry |
| `docs/CODE_DOCUMENTATION.md` | Document the Nginx proxy architecture |

## What NOT to Change

- No application Python code changes
- No frontend JavaScript changes
- No database or migration changes
- Do not change uvicorn's CMD in the Dockerfile
- Do not remove the `StaticCacheMiddleware` — it's still useful for non-Docker dev

## Context: Why This Fix

Previous performance work (connection pooling, circuit breakers, staggered pollers, interruptible SSE sleep, Redis pub/sub polling) addressed server-side contention. But the remaining 30–50s stalls are caused by HTTP/1.1 protocol limitations that can't be fixed in application code. HTTP/2 multiplexing is the correct architectural solution.

The SSE-specific fixes (interruptible sleep in `stream.py`, `get_message` polling in `state_manager.py`) remain valuable — they reduce server-side resource holding time and improve disconnect detection regardless of protocol version.

## Risk Assessment

- **Low risk.** No application code changes. Nginx is battle-tested for reverse proxying.
- **Self-signed cert warning:** Expected in dev. Users accept once and it's remembered. Production already uses TLS on port 8443 via systemd.
- **Fallback:** If anything goes wrong, revert `docker-compose.yml` to re-expose port 8000 directly and the app works exactly as before.
