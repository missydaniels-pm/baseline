# Why a Dockerfile instead of Railway's auto-detection (added 8/12/26):
#
# Railway's railpack builder uses `mise` to install Python at build time. On
# 2026-08-12 that step failed repeatedly — first on python@3.13.14 (the version
# it picked on its own), then on python@3.10.19 after we pinned it — both with
# the same transport error fetching the tarball:
#
#   mise ERROR Failed to install core:python@...: error sending request:
#   http2 error: stream error received: refused stream
#
# Same mise build (2026.8.4, released 2026-08-11), two different versions, no
# Railway status incident. Pulling a prebuilt image from a registry avoids that
# download path completely, and pins the runtime for real: no more silent drift
# between what dev tests on and what production runs (the builder had drifted to
# 3.13 while development is on 3.10.7).
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependencies first so a code-only change reuses this layer.
# No apt packages needed: psycopg2-binary, Pillow and bcrypt all ship manylinux
# wheels. If a dependency ever needs compiling, add build-essential here rather
# than switching off -slim.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway injects PORT (8080 on this service — confirmed in the deploy logs) and
# gunicorn binds it, so the fallback below is only for running this image
# outside Railway. 8080 matches Railway's convention.
#
# NOTE: the container listening on the right port is only half of it. Railway's
# public domain has its own target port, and if that still points at whatever
# the previous deployment used, every request 502s while the deployment shows
# ACTIVE and the logs look perfectly healthy. "Deployed" and "reachable" are
# different things.
EXPOSE 8080

# Shell form so $PORT expands.
# --timeout 120 because the AI check-in makes the Anthropic call in-request and
# can exceed gunicorn's 30s default, which would turn working check-ins into
# 502s. One worker, matching the previous single-process behaviour: adding
# workers would make the startup migrations run concurrently, which is a
# separate change and not one to bundle in here.
CMD gunicorn app:app --bind 0.0.0.0:${PORT:-8080} --timeout 120
