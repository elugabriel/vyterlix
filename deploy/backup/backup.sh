#!/usr/bin/env bash
# Nightly backup: the database and the uploaded files, kept for 14 days.
# Install: sudo cp backup.sh /usr/local/bin/vyterlix-backup && sudo chmod 750 /usr/local/bin/vyterlix-backup
# Cron (as root):  15 2 * * * /usr/local/bin/vyterlix-backup >> /var/log/vyterlix-backup.log 2>&1
set -euo pipefail

DEST=/var/backups/vyterlix
KEEP_DAYS=14
STAMP=$(date +%Y-%m-%d_%H%M)
mkdir -p "$DEST"
umask 077

# Custom format: smaller, and restorable table by table.
sudo -u postgres pg_dump --format=custom --file="$DEST/db_$STAMP.dump" vyterlix
tar -czf "$DEST/uploads_$STAMP.tar.gz" -C /var/lib/vyterlix uploads

# Check the dump can be read before trusting it.
pg_restore --list "$DEST/db_$STAMP.dump" > /dev/null

find "$DEST" -type f \( -name 'db_*.dump' -o -name 'uploads_*.tar.gz' \) -mtime +"$KEEP_DAYS" -delete
echo "$(date -Is) backup ok: db_$STAMP.dump"

# Copy off the server too (a backup on the same machine is lost with the machine). Fill in once a
# destination exists, for example a second server or storage bucket:
# rclone copy "$DEST" remote:vyterlix-backups --max-age 2d
