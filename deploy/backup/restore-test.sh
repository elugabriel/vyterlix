#!/usr/bin/env bash
# Proves a backup works: restores the newest dump into a throw-away database and counts the tables.
# Run it after the first backup and then every month. It never touches the live database.
# Usage: sudo ./restore-test.sh [/var/backups/vyterlix/db_YYYY-MM-DD_HHMM.dump]
set -euo pipefail

DUMP=${1:-$(ls -t /var/backups/vyterlix/db_*.dump | head -1)}
TEST_DB=vyterlix_restore_test
echo "Restoring $DUMP into $TEST_DB"

sudo -u postgres dropdb --if-exists "$TEST_DB"
sudo -u postgres createdb "$TEST_DB"
sudo -u postgres pg_restore --no-owner --dbname="$TEST_DB" "$DUMP"

TABLES=$(sudo -u postgres psql -At -d "$TEST_DB" -c "select count(*) from information_schema.tables where table_schema='public'")
USERS=$(sudo -u postgres psql -At -d "$TEST_DB" -c "select count(*) from users")
echo "Restored: $TABLES tables, $USERS users"

sudo -u postgres dropdb "$TEST_DB"
echo "Restore test passed. Test database removed."
