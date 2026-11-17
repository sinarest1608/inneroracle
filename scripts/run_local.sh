#!/bin/sh
# Run InnerOracle on this Mac for testing, completely apart from production:
# its own push database (state/push.local.db), its own VAPID keys and admin
# token (.env.local, created on first run and git-ignored), no Cloudflare
# tunnel, canned model replies, festival jobs off, and the /dev test helpers on.
#
#   scripts/run_local.sh            # http://localhost:8000/ui/
#   PORT=8001 scripts/run_local.sh
#
# The admin token for http://localhost:8000/ui/admin/announce.html is the
# PUSH_ADMIN_TOKEN line in .env.local.
set -eu
cd "$(dirname "$0")/.."
[ -d .venv ] && . .venv/bin/activate

if [ ! -f .env.local ]; then
  {
    echo "# Local testing only. Never copy these to the server, or the server's here."
    python scripts/gen_vapid_keys.py
    cat <<'VARS'
PUSH_ENABLED=true
PUSH_DB_PATH=state/push.local.db
VAPID_SUBJECT=https://inneroracle.cc
LOCAL_DEV=1
MOCK_MODEL=1
FESTIVAL_VERSES_ENABLED=false
ALERT_SMTP_USER=
ALERT_SMTP_PASSWORD=
PUBLIC_BASE_URL=http://localhost:8000
VARS
  } > .env.local
  chmod 600 .env.local
  echo "Created .env.local (local keys and admin token)."
fi

set -a
. ./.env.local
set +a
echo "Admin token: $PUSH_ADMIN_TOKEN"
exec uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8000}"
