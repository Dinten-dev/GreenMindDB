# Plant Wiki - Growing Conditions Database

Admin editor for plant growing conditions with authentication and audit logging.

## Quick Start

```bash
docker compose up -d

# Website (read-only without login)
open http://localhost:3000

# API docs
open http://localhost:8000/docs
```

## Features

- **Read-only public access** - Browse plants without login
- **Authenticated editing** - Login required for CRUD
- **Audit logging** - Full change history per plant
- **Source management** - URL or "Own Experience" per condition

## Architecture

| Service | Tech | Port |
|---------|------|------|
| Database | PostgreSQL | 5432 |
| Backend | FastAPI | 8000 |
| Frontend | Next.js | 3000 |

## Authentication

### Security
- **Password hashing**: bcrypt via passlib
- **Tokens**: JWT (7-day expiry)
- **Rate limiting**: 5 requests/min on login/signup
- **No user enumeration**: Generic error messages

### Endpoints
| Endpoint | Description |
|----------|-------------|
| `POST /auth/signup` | Create account (email + password 8+ chars) |
| `POST /auth/login` | Get JWT token |
| `GET /auth/me` | Current user (requires auth) |

### Database
```sql
users(id, email, password_hash, created_at, is_active)
```

## API Permissions

| Endpoint | Auth Required |
|----------|---------------|
| `GET /species`, `/metrics`, `/sources` | No |
| `POST /species`, `PUT`, `DELETE` | Yes |
| `POST /target-ranges`, `PUT`, `DELETE` | Yes |
| `GET /species/{id}/history` | No |

## Source Handling

When adding conditions, choose:
1. **URL source** - Enter URL (required), optional title/publisher
2. **Own experience** - Optional notes

Sources are deduplicated by URL automatically.

## Audit Log

Every change is logged:
```sql
audit_log(
  id, timestamp, user_id,
  entity_type,  -- 'species' or 'target_range'
  entity_id, species_id,
  action,  -- 'CREATE', 'UPDATE', 'DELETE'
  diff_json  -- { before: {...}, after: {...} }
)
```

### History API
```bash
GET /species/{id}/history?limit=50
```

Returns:
```json
[{
  "timestamp": "2026-02-06T15:30:00Z",
  "user_email": "user@example.com",
  "action": "UPDATE",
  "diff_json": {
    "before": { "optimal_low": 15, "optimal_high": 21 },
    "after": { "optimal_low": 16, "optimal_high": 22 }
  }
}]
```

## Development

```bash
# Backend
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload

# Frontend
cd frontend && npm install && npm run dev
```
