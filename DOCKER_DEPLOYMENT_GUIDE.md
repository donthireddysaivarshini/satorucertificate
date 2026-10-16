# Satoru Certificate System — Docker Deployment Guide

This guide provides end-to-end instructions for deploying the **Satoru Certificate Management System (Certdesk)** using Docker on any PC or server, especially environments already running multiple Docker containers where default ports (like 8000, 80, or 443) might already be taken.

---

## 1. Application Architecture & Overview

Certdesk is an enterprise certificate generation, approval, and verification platform for Satoru Foundation:
* **`web`**: Django WSGI application run by Gunicorn. Serves the administration dashboard, batch management, template editor, and public QR verification endpoints (`/v/<code>/` and `/verify/`).
* **`worker`**: Dedicated background worker container (`python manage.py worker`). Automatically picks up asynchronous tasks to render Ultra-HD 300 DPI PDFs and send personalized batch emails.
* **`db`**: PostgreSQL 16 database container (can also be switched to Neon cloud Postgres via `DATABASE_URL`).
* **`media` volume**: Shared Docker volume where generated certificate PDFs, batch CSVs, and template assets reside.

---

## 2. Pre-Flight Check: Find Free Ports

On systems already running multiple Docker containers, common ports like `8000`, `80`, `443`, or `5432` are frequently occupied. Follow these steps to find open ports before launching.

### Step 2.1: See Which Ports Existing Docker Containers Are Using
Run this command in your terminal:
```bash
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```
This lists every active container and its published host ports (e.g., `0.0.0.0:8000->8000/tcp`).

---

### Step 2.2: Test If a Specific Port Is Free

#### On Windows (PowerShell):
Check if port 8000 or 8080 is listening:
```powershell
# Check port 8000
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue

# Or check using netstat
netstat -ano | findstr :8000
netstat -ano | findstr :8080
```
* If the command returns **nothing**, the port is **FREE** to use.
* If it returns listening processes with a PID, that port is occupied.

Quick PowerShell snippet to find the next available port from 8000-8099:
```powershell
8000..8099 | Where-Object { -not (Get-NetTCPConnection -LocalPort $_ -ErrorAction SilentlyContinue) } | Select-Object -First 3
```

#### On Linux / macOS / WSL:
```bash
# Check if port 8000 or 8080 is listening
sudo lsof -i :8000
sudo lsof -i :8080

# Or using ss
ss -tulpn | grep 8000
```
* If nothing is printed, the port is **FREE**.

Quick Linux bash one-liner to find 3 open ports:
```bash
for port in {8080..8099}; do ! (nc -z localhost $port 2>/dev/null) && echo "Port $port is free"; done | head -n 3
```

---

## 3. Configuration (`.env`)

In the project folder, copy `.env.example` to `.env`:

```bash
# Linux / macOS / Git Bash
cp .env.example .env

# Windows PowerShell / CMD
copy .env.example .env
```

Open `.env` in a text editor (Notepad, VS Code, or nano) and configure:

### A. Set Your Free Web Port
Set `WEB_PORT` to any free port you found in Step 2 (e.g. `8080`, `8088`, or `9000`):
```env
WEB_PORT=8080
SITE_URL=http://localhost:8080
```
*(If accessing across a local office Wi-Fi / LAN, you can use your machine's IP, e.g. `SITE_URL=http://192.168.1.50:8080`)*

### B. Choose Your Database Mode

#### Option 1: Standalone Local PostgreSQL (Recommended for isolated local PC)
Leave `DATABASE_URL` commented out. Docker Compose will start the bundled `db` container automatically:
```env
POSTGRES_DB=certdesk
POSTGRES_USER=certdesk
POSTGRES_PASSWORD=satoru_secure_local_password_2026
```

#### Option 2: Neon Cloud Database (Recommended if sharing data across PCs)
Uncomment and supply your Neon connection string:
```env
DATABASE_URL=postgresql://neondb_owner:npg_cSMx6f7gsdNX@ep-shiny-mode-b5p5ybtq.c-7.us-east-2.aws.neon.tech/neondb?sslmode=require
```

### C. Email Configuration (SMTP)

#### Safe Testing Mode (Console / Logs):
```env
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
DEFAULT_FROM_EMAIL=office@satorufoundation.org
```
Emails will be written directly to the `worker` Docker logs without sending to actual recipients.

#### Live Delivery Mode (Gmail / Google Workspace):
```env
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=1
EMAIL_HOST_USER=office@satorufoundation.org
EMAIL_HOST_PASSWORD=your-16-character-app-password
DEFAULT_FROM_EMAIL=office@satorufoundation.org
```

---

## 4. Deploying with Docker Compose

Run the build and start command from the project root directory:

```bash
docker compose up -d --build web worker db
```

> **Why only `web worker db`?**  
> On machines already running other web containers, port 80 and 443 are almost always taken by existing reverse proxies (Nginx, Traefik, Apache). Starting `web`, `worker`, and `db` binds **only** your designated `WEB_PORT` (e.g. 8080) and avoids any port collisions.  
> *(If port 80 and 443 are completely free on that PC and you want automatic SSL via Caddy, simply run `docker compose up -d --build`)*

---

## 5. First-Time Setup & Admin User

Once the containers are up, initialize your admin user:

```bash
docker compose exec web python manage.py createsuperuser
```
Follow the interactive prompts:
* **Username:** (e.g., `satoruadmin`)
* **Email:** (e.g., `office@satorufoundation.org`)
* **Password:** (enter a secure password)

The database roles (`Organisers`, `Approvers`) and database migrations run automatically on startup.

---

## 6. Accessing & Testing the Deployment

### Test 1: Open the Admin Dashboard
Open your web browser and go to:
```text
http://localhost:8080/admin/
```
*(Replace `8080` with your configured `WEB_PORT`)*  
Log in with the superuser credentials you created in Step 5.

### Test 2: Check Running Container Health
```bash
docker compose ps
```
You should see:
* `certdesk-web`: Up, port `0.0.0.0:8080->8000/tcp`
* `certdesk-worker`: Up
* `certdesk-db`: Up (healthy)

### Test 3: View Application & Worker Logs
To check the web server logs:
```bash
docker compose logs -f web
```
To monitor background PDF rendering or email operations:
```bash
docker compose logs -f worker
```
*(Press `Ctrl+C` to exit log streaming)*

### Test 4: Verify Public Verification URL
Open in browser:
```text
http://localhost:8080/verify/
```
You should see the Satoru Certificate Verification page with the search form.

---

## 7. Useful Management Commands

| Action | Command |
|---|---|
| **Stop the application** | `docker compose stop` |
| **Restart the application** | `docker compose restart` |
| **View real-time logs** | `docker compose logs -f` |
| **Rebuild containers after code changes** | `docker compose up -d --build web worker` |
| **Open Django Shell inside container** | `docker compose exec web python manage.py shell` |
| **Run tests inside container** | `docker compose exec web python manage.py test certs` |
| **Backup Database (Local Postgres mode)** | `docker compose exec db pg_dump -U certdesk certdesk > backup.sql` |
| **Tear down completely (preserves data)** | `docker compose down` |
| **Tear down and delete all volumes** *(CAUTION)* | `docker compose down -v` |

---

## 8. Common Troubleshooting

### 1. `Bind for 0.0.0.0:8000 failed: port is already allocated`
* **Cause:** Another container or local process is already using that port.
* **Fix:** Open `.env`, change `WEB_PORT` to an unused port (e.g. `WEB_PORT=8089`), and restart:
  ```bash
  docker compose up -d web
  ```

### 2. PDFs look blurry or low resolution
* Certdesk has been upgraded with Ultra-HD 300 DPI rendering (`quality=100, subsampling=0`). All newly created batches use high-resolution rendering by default.

### 3. Emails not arriving in inboxes
* Check if `.env` has `EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend`. In console mode, check the output in `docker compose logs worker`.
* For live emails, verify `EMAIL_HOST_USER` and Google App Password in `.env`, then run `docker compose restart worker`.
