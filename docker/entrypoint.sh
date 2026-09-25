#!/usr/bin/env bash
# wedoc container entrypoint.
#   serve         (default) run migrations (via app lifespan) + HTTP/WS server
#   migrate-only  apply DB migrations then exit 0 (parity with reference image)
#   worker        run the arq background worker
set -euo pipefail

cmd="${1:-serve}"

case "$cmd" in
  migrate-only)
    exec python -c "import asyncio; from wedoc.config import get_settings; from wedoc.db.migrator import migrate_all; asyncio.run(migrate_all(get_settings().meta_database_dsn))"
    ;;
  worker)
    exec wedoc-worker
    ;;
  serve)
    exec wedoc
    ;;
  *)
    exec "$@"
    ;;
esac
