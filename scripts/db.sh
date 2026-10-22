#!/usr/bin/env bash
# Inspect the dispatcher's own database.
#
# The database lives in a Docker volume, not on your machine. These commands
# read it without stopping anything and without writing to it.
#
#   ./scripts/db.sh tables                 list our tables and row counts
#   ./scripts/db.sh query "SELECT ..."     run any read-only query
#   ./scripts/db.sh snapshot [file]        copy a consistent snapshot to your machine
#   ./scripts/db.sh open [file]            open a snapshot with sqlite3 (or your DB browser)
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

DB_PATH="/var/lib/loadpilot/dispatcher.db"

compose() { docker compose --project-directory "$PROJECT_DIR" -f "$PROJECT_DIR/compose.yaml" "$@"; }

# Run a read-only query inside the backend container.
run_query() {
  compose exec -T backend python - "$1" <<'PY'
import sqlite3
import sys

sql = sys.argv[1]
# Read-only: the snapshot can never be modified from here.
connection = sqlite3.connect('file:/var/lib/loadpilot/dispatcher.db?mode=ro', uri=True)
connection.row_factory = sqlite3.Row
try:
    rows = connection.execute(sql).fetchall()
except sqlite3.Error as error:
    print(f'SQL error: {error}')
    raise SystemExit(1)
if not rows:
    print('(no rows)')
for row in rows:
    print(' | '.join(f'{key}={row[key]}' for key in row.keys()))
PY
}

case "${1:-tables}" in
  tables)
    run_query "SELECT name AS table, (SELECT COUNT(*) FROM pragma_table_info(m.name)) AS columns FROM sqlite_master m WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name <> 'alembic_version' ORDER BY name"
    echo
    echo "Row counts:"
    for table in $(compose exec -T backend python -c "
import sqlite3
connection = sqlite3.connect('file:/var/lib/loadpilot/dispatcher.db?mode=ro', uri=True)
print(' '.join(name for (name,) in connection.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name <> 'alembic_version' ORDER BY name\")))
"); do
      count=$(compose exec -T backend python -c "
import sqlite3
connection = sqlite3.connect('file:/var/lib/loadpilot/dispatcher.db?mode=ro', uri=True)
print(connection.execute('SELECT COUNT(*) FROM \"$table\"').fetchone()[0])
")
      printf '  %-30s %s\n' "$table" "$count"
    done
    ;;
  query)
    if [ $# -lt 2 ]; then echo "Usage: ./scripts/db.sh query \"SELECT ...\"" >&2; exit 1; fi
    run_query "$2"
    ;;
  snapshot)
    target="${2:-$PROJECT_DIR/backend/data/dispatcher-snapshot.db}"
    echo "Making a consistent snapshot of the live database..."
    compose exec -T backend python -c "
import sqlite3
source = sqlite3.connect('file:${DB_PATH}?mode=ro', uri=True)
target = sqlite3.connect('/tmp/dispatcher-snapshot.db')
source.backup(target)
target.close()
source.close()
"
    mkdir -p "$(dirname "$target")"
    compose cp backend:/tmp/dispatcher-snapshot.db "$target" >/dev/null
    compose exec -T backend rm -f /tmp/dispatcher-snapshot.db
    echo "Snapshot written to: $target"
    echo "Open it with: ./scripts/db.sh open \"$target\""
    ;;
  open)
    target="${2:-$PROJECT_DIR/backend/data/dispatcher-snapshot.db}"
    if [ ! -f "$target" ]; then
      echo "No snapshot at $target. Run './scripts/db.sh snapshot' first." >&2
      exit 1
    fi
    if command -v sqlite3 >/dev/null 2>&1; then
      exec sqlite3 -header -column "$target"
    fi
    echo "sqlite3 is not installed. Open this file in any SQLite browser:"
    echo "  $target"
    ;;
  *)
    sed -n '2,12p' "$0"
    exit 1
    ;;
esac
