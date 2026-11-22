# Putting Vyterlix on Hostinger

Written for one **Hostinger VPS** (Linux, Ubuntu 24.04), no Docker. Hostinger's ordinary web hosting
cannot run this (it needs a database server, a background worker and a Python app), so it must be a
**VPS plan**. At checkout choose the **United Kingdom (London)** data centre if it is offered.

Everything on one server: nginx (the website and HTTPS) -> the API (localhost:8000) -> PostgreSQL
(localhost only). A second service, the **worker**, does the background jobs.

Status: **prepared, not yet run on a real server.** Every command below is untested until the VPS
exists; do the first run as a rehearsal and fix this file where it is wrong.

## Try it on your own computer first (an empty site)
Proves the first-admin step on a site with nothing in it. Needs the PostgreSQL superuser password once:
```powershell
psql -U postgres -c "CREATE DATABASE vyterlix_rehearsal OWNER vyterlix;"
$env:VYTERLIX_DATABASE_URL = "postgresql+psycopg://vyterlix:YOUR-DB-PASSWORD@localhost:5432/vyterlix_rehearsal"
cd backend
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m app.cli.staff create falusidorcas@gmail.com --name "Dorcas Falusi" --role admin
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000      # then open the website and sign in
```
Afterwards: `psql -U postgres -c "DROP DATABASE vyterlix_rehearsal;"` and close that window, so the setting does not
carry over. (On 22 Nov 2026 this was rehearsed against an emptied copy: all 75 migrations built the tables, the
admin signed in to an empty site, saw "You don't belong to any business yet", created a business, and opened Admin.)

## What you need before starting
- [ ] Hostinger VPS (KVM 2 or bigger is comfortable: 2 CPUs, 8 GB) with Ubuntu 24.04, London.
- [ ] `vyterlix.com` pointed at the VPS: an **A record** for `@` and `www` to the server's IP.
- [ ] An SMTP email service, and the mailbox `admin@vyterlix.com` (the address Vyterlix sends from) created on `vyterlix.com` with SPF and DKIM set up.
- [ ] Fresh secrets (see `env.production.example`). Do not reuse any secret that has been pasted in chat.
- [ ] Your SSH key added in Hostinger's panel, so there is no password login.

## 1. First login and safety
```bash
ssh root@SERVER_IP
apt update && apt -y upgrade
adduser --disabled-password --gecos "" vyterlix
ufw allow OpenSSH && ufw allow 'Nginx Full' && ufw enable
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config && systemctl restart ssh
apt -y install nginx postgresql python3-venv python3-dev libpq-dev build-essential git certbot python3-certbot-nginx
```
Hostinger's panel also has a firewall and automatic weekly snapshots: turn both on.

## 2. Database (on the server only)
```bash
sudo -u postgres psql -c "CREATE USER vyterlix WITH PASSWORD 'CHOOSE-A-LONG-PASSWORD';"
sudo -u postgres psql -c "CREATE DATABASE vyterlix OWNER vyterlix;"
```
It starts **empty**. Never copy the development database: it holds demo data.

## 3. The code
```bash
mkdir -p /srv/vyterlix /var/lib/vyterlix/uploads /etc/vyterlix
git clone https://github.com/elugabriel/vyterlix.git /srv/vyterlix      # private repo: use a deploy key
chown -R vyterlix:vyterlix /srv/vyterlix /var/lib/vyterlix
sudo -u vyterlix python3 -m venv /srv/vyterlix/backend/.venv
sudo -u vyterlix /srv/vyterlix/backend/.venv/bin/pip install -e /srv/vyterlix/backend
```

## 4. Settings
```bash
cp /srv/vyterlix/deploy/env.production.example /etc/vyterlix/vyterlix.env
nano /etc/vyterlix/vyterlix.env           # replace every CHANGE-ME
chown root:vyterlix /etc/vyterlix/vyterlix.env && chmod 640 /etc/vyterlix/vyterlix.env
```
Keep a copy of `VYTERLIX_ENCRYPTION_KEY` **off the server** (a password manager). Without it, saved
connections to Xero, Google and the rest cannot be read again.

## 5. Create the tables
```bash
cd /srv/vyterlix/backend
sudo -u vyterlix bash -c 'set -a; . /etc/vyterlix/vyterlix.env; set +a; .venv/bin/alembic upgrade head'
```

## 5b. The first admin (your client)
A new site has no accounts, so there is nobody who could grant the first admin. This makes the account,
already verified, and gives it admin rights in one step. It asks for the password without showing it,
and never puts it on the command line or in a file:
```bash
cd /srv/vyterlix/backend
sudo -u vyterlix bash -c 'set -a; . /etc/vyterlix/vyterlix.env; set +a; .venv/bin/python -m app.cli.staff create falusidorcas@gmail.com --name "Dorcas Falusi" --role admin'
```
She signs in at the ordinary login page, sees an **Admin** link in the top bar, and can create her
business (Dolaris Limited) from "Your businesses". She should change the password in **Your account**
after her first sign-in. Use `--role support` for someone who may look but not change.

## 6. Start the app and the worker
```bash
cp /srv/vyterlix/deploy/systemd/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now vyterlix-api vyterlix-worker
curl -s http://127.0.0.1:8000/api/v1/health/ready        # {"status":"ready","database":"ok"}
```
The app **refuses to start** in production with the development secrets, a wildcard CORS list or the
console email backend. That is deliberate: a start-up error naming one of those means a setting is missing.

## 7. The website and HTTPS
```bash
cp /srv/vyterlix/deploy/frontend-config.js /srv/vyterlix/frontend/js/config.js
cp /srv/vyterlix/deploy/nginx/vyterlix-proxy.conf /etc/nginx/vyterlix-proxy.conf
cp /srv/vyterlix/deploy/nginx/vyterlix.conf /etc/nginx/sites-available/vyterlix
ln -s /etc/nginx/sites-available/vyterlix /etc/nginx/sites-enabled/vyterlix
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
certbot --nginx -d vyterlix.com -d www.vyterlix.com --redirect -m YOUR-EMAIL --agree-tos
```
Certbot renews by itself (`systemctl list-timers | grep certbot`).
`git pull` would overwrite `config.js`, so the update script (step 9) copies it back each time.

## 8. Backups (do this on day one)
```bash
cp /srv/vyterlix/deploy/backup/backup.sh /usr/local/bin/vyterlix-backup
chmod 750 /usr/local/bin/vyterlix-backup
(crontab -l 2>/dev/null; echo "15 2 * * * /usr/local/bin/vyterlix-backup >> /var/log/vyterlix-backup.log 2>&1") | crontab -
/usr/local/bin/vyterlix-backup                         # run once now
bash /srv/vyterlix/deploy/backup/restore-test.sh       # prove the backup restores
```
A backup kept only on the server is lost with the server: set up the off-server copy at the bottom of
`backup.sh` (a second server or storage bucket) before real customers arrive.

## 9. Updating the site later
```bash
cd /srv/vyterlix && sudo -u vyterlix git pull
sudo -u vyterlix /srv/vyterlix/backend/.venv/bin/pip install -e backend
sudo -u vyterlix bash -c 'cd backend && set -a; . /etc/vyterlix/vyterlix.env; set +a; .venv/bin/alembic upgrade head'
cp deploy/frontend-config.js frontend/js/config.js
systemctl restart vyterlix-api vyterlix-worker
```
Take a backup first (`vyterlix-backup`) whenever a release changes the database.

## 10. Check it is healthy
- `https://vyterlix.com` loads and `https://vyterlix.com/api/v1/health/ready` says ready.
- Register the first account (the owner), confirm the email arrives, sign in, add a sale.
- `systemctl status vyterlix-api vyterlix-worker` both active; `journalctl -u vyterlix-worker -n 50`.
- Register `https://vyterlix.com/integrations-callback.html` in the Xero, QuickBooks, Zoho, Google,
  TrueLayer and Meta consoles, then try each connection once.

## Known gaps (not done yet)
- Not rehearsed on a real server. Not tested: the content-security-policy in `nginx/vyterlix.conf`
  against every page (open each page with the browser console on and look for blocked items).
- No monitoring or alert if the worker stops or jobs wait too long (checklist L3).
- The phone apps still point at the development address; they need the production address and store accounts.
- Staff accounts for the admin pages are created with `python -m app.cli.staff` (see its help).
