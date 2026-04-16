# CC Task: Fix CRLF Line Endings Breaking nginx Container

## Problem

The nginx container fails to start because `nginx/generate-cert.sh` has Windows CRLF line endings. Alpine's `/bin/sh` interprets the shebang as `#!/bin/sh\r`, which doesn't resolve, causing:

```
/docker-entrypoint.sh: line 31: /docker-entrypoint.d/10-generate-cert.sh: not found
```

The file is present but not executable due to the embedded `\r` characters.

## Root Cause

Git on Windows checks out `generate-cert.sh` with CRLF line endings. The `.gitattributes` file only has `*.bat text eol=crlf` — no rule to enforce LF on shell scripts. When Docker copies the file into the Alpine container, the `\r` bytes break the shell interpreter.

## Fix — Two Parts

### Part 1: Add `.gitattributes` rules for shell scripts

**File:** `.gitattributes`

Add this line after the existing `*.bat` rule:

```
*.sh text eol=lf
```

The full file should read:

```
*.bat text eol=crlf
*.sh text eol=lf
```

### Part 2: Normalize `nginx/generate-cert.sh` line endings

After updating `.gitattributes`, run:

```bash
git add .gitattributes
git rm --cached nginx/generate-cert.sh
git add nginx/generate-cert.sh
```

This forces Git to re-normalize the file with the new eol=lf rule. Verify the fix:

```bash
git diff --cached nginx/generate-cert.sh
```

You should see the file re-added. Check there are no `^M` (carriage return) characters visible in the diff.

### Alternative Dockerfile Hardening (Optional)

As a belt-and-suspenders approach, you could also add a `sed` line in `nginx/Dockerfile` to strip CRLFs during build. This makes the build immune regardless of local Git config:

**File:** `nginx/Dockerfile` — add after the COPY line:

```dockerfile
COPY generate-cert.sh /docker-entrypoint.d/10-generate-cert.sh
RUN sed -i 's/\r$//' /docker-entrypoint.d/10-generate-cert.sh && \
    chmod +x /docker-entrypoint.d/10-generate-cert.sh
```

This replaces the existing `RUN chmod +x` line (combine into one RUN to keep layers minimal).

## Commit Message

```
fix: enforce LF line endings on shell scripts to fix nginx container startup

nginx/generate-cert.sh was checked out with CRLF on Windows, causing
Alpine's /bin/sh to fail with "not found" on the entrypoint script.

- Add *.sh eol=lf rule to .gitattributes
- Re-normalize generate-cert.sh
- Harden Dockerfile with sed CR strip as fallback
```

## Verification

After committing, rebuild from scratch:

```bash
docker compose down
docker compose up -d --build nginx
docker compose logs nginx
```

Expected: cert generation message (or "already exists" skip), no "not found" error. Then confirm the full stack:

```bash
docker compose ps
```

All four services (nginx, smp, postgres, redis) should show as running with nginx on `0.0.0.0:8443->8443/tcp`.

## Risk

None — `.gitattributes` change only affects checkout behavior for `.sh` files. The Dockerfile `sed` is a no-op on files that already have LF endings.
