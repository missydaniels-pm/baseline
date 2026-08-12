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

# THIS FILE IS THE ONLY IN-REPO DEFINITION OF HOW BASELINE STARTS. Railway builds
# from this Dockerfile (confirmed in the build log: "load build definition from
# Dockerfile"), so a Procfile or a railpack/mise start command would be read by
# nothing. The Procfile added on 8/12/26 was deleted for exactly that reason —
# a start command that is written down but never executed is what hid the
# debug-server bug for months. If you change how the app starts, change it here.
#
# ONE override lives OUTSIDE the repo: Railway's dashboard "Custom Start Command"
# (Settings -> Deploy) supersedes this CMD at the deploy layer and does NOT show
# in the build log. It must be blank for this file to be what actually runs —
# STAGING_SETUP.md's verification list has the check. Don't upgrade this comment
# back to an unqualified "only definition": that field is the exact kind of
# out-of-band start command this whole cleanup is about.
#
# `sh -c` + explicit `exec`. This is HARDENING, NOT A BUG FIX — read on before
# assuming it repaired something, because the obvious story about it is wrong.
#
# The tempting claim (and the one the build log's JSONArgsRecommended warning
# invites) is: shell form leaves /bin/sh as PID 1 with gunicorn as its child, sh
# never forwards SIGTERM, so redeploys SIGKILL workers mid-request. That was
# tested on 8/13/26 and did NOT reproduce: a POSIX shell given `sh -c '<single
# command>'` implicit-execs it, so gunicorn replaced the shell under BOTH the
# plain and the exec form. Verified with a control (`sh -c 'gunicorn ...; true'`,
# which the shell cannot optimize) that did show `sh` as the wrapper, proving the
# test could detect the failure case. dash does the same optimization, so
# redeploys were almost certainly already draining.
#
# Keeping `exec` anyway: it makes the guarantee explicit rather than resting on a
# shell optimization that is conventional but not contractual, and any future
# edit that appends a second command to this line (`... ; something`) would
# silently reintroduce the real hazard. The `sh -c` wrapper keeps ${PORT}
# expansion, which a bare JSON array would lose.
#
# NOT verified: PID 1 inside the actual Railway container. `railway ssh` +
# `cat /proc/1/comm` would settle it and needs an SSH key generated first.
#
# --timeout 120: see above — gunicorn's 30s default would turn working check-ins
# into 502s. One worker, matching the previous single-process behaviour: adding
# workers would make the startup migrations run concurrently, which is a
# separate change and not one to bundle in here.
CMD ["sh", "-c", "exec gunicorn app:app --bind 0.0.0.0:${PORT:-8080} --timeout 120"]
