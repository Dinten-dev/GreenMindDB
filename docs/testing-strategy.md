# Testing Strategy

> GreenMind testing approach and standards.

---

## Overview

| Layer | Tool | Scope | Status |
|-------|------|-------|--------|
| **Backend Unit** | pytest | Config, auth, endpoints, services | ✅ In place |
| **Backend Integration** | pytest + Docker Compose | Full stack with database | ✅ In place |
| **Frontend Lint** | ESLint (via Next.js) | Type and style checks | ✅ In place |
| **Frontend Type-check** | TypeScript (`tsc --noEmit`) | Static type verification | ✅ In place |
| **Frontend Build** | Next.js build | Compilation + optimization | ✅ In place |
| **Frontend Unit** | Jest / Vitest | Component and hook testing | ⏳ Future |

---

## Backend Testing

### Unit Tests

Located in `backend/tests/`. Run with:

```bash
make test
# or: cd backend && python -m pytest tests/ -v
```

**Current coverage:**

| Test File | What It Tests |
|-----------|---------------|
| `test_health.py` | Health endpoint, root endpoint, security headers |
| `test_config.py` | Settings validation (JWT secret, CORS parsing, production safety) |
| `test_auth_utils.py` | Password hashing (bcrypt), JWT token create/decode/expiry/tampering |

**Conventions:**
- Use `TestClient` from FastAPI for endpoint tests
- Group related tests in classes (`TestHealthEndpoint`, `TestJWTTokens`, etc.)
- Use descriptive test names: `test_expired_token_returns_none`
- Skip Docker-dependent tests with `SKIP_DOCKER_TESTS=1`

### Integration Tests

Located in `backend/tests/test_macmini_stack.py`. These spin up the full Docker Compose stack and test end-to-end flows.

```bash
cd backend && pytest tests/test_macmini_stack.py -v
```

Requires Docker running locally. Set `SKIP_DOCKER_TESTS=1` to skip in CI.

### Adding New Tests

```python
# backend/tests/test_my_feature.py
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

class TestMyFeature:
    def test_success_case(self):
        response = client.get("/api/my-endpoint")
        assert response.status_code == 200

    def test_error_case(self):
        response = client.get("/api/bad-request")
        assert response.status_code == 400
```

---

## Frontend Testing

### Current Quality Gates

1. **Lint**: `npm run lint` (ESLint via Next.js)
2. **Type-check**: `npm run type-check` (TypeScript compiler)
3. **Build**: `npm run build` (validates full compilation)

### Future: Component Testing

When adding frontend tests, use:
- **Vitest** or **Jest** as the test runner
- **React Testing Library** for component testing
- Focus on user-facing behavior, not implementation details

---

## CI Integration

All tests run automatically in the GitHub Actions CI pipeline on every push and PR:

1. Backend: `ruff check` → `black --check` → `pytest`
2. Frontend: `npm run lint` → `npm run build`

See `.github/workflows/ci.yml` for full configuration.

---

## Test Principles

1. **Test behavior, not implementation** – test what the user/API consumer sees
2. **Keep tests fast** – unit tests should complete in seconds
3. **Test the happy path AND edge cases** – include validation, auth, error scenarios
4. **No test should depend on another** – each test is independently runnable
5. **Don't test framework code** – focus on your business logic
