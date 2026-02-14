# Plant Wiki Deployment Guide

This package contains the fully dockerized Plant Wiki application.

## 1. Requirements
* Docker
* Docker Compose

## 2. Quick Start
1. Unzip this package on your server.
2. Open a terminal in the folder.
3. Create runtime config:
   ```bash
   cp .env.example .env
   ```
4. Edit `.env` and set at least:
   - `POSTGRES_PASSWORD`
   - `JWT_SECRET_KEY` (32+ chars)
5. Run the following command:
   ```bash
   docker compose up -d --build
   ```
6. Wait a few minutes for the initial build to complete.

## 3. Access the Application
* **Website**: `http://<YOUR-SERVER-IP>:3000`
* **API Documentation**: `http://<YOUR-SERVER-IP>:8000/docs`

## 4. Features
* **Automatic IP Detection**: No need to configure IP addresses; the app adapts to the server.
* **Persisted Data**: Database files are stored in a Docker volume `postgres_data`.
* **Hardened Containers**: `no-new-privileges`, dropped capabilities, non-root backend.

## 5. Troubleshooting
If containers don't start, check logs:
```bash
docker compose logs -f
```
