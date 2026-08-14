# Contributing to GreenMind

Thank you for contributing to GreenMind! This document outlines our workflow, conventions, and standards.

## Table of Contents

- [Development Setup](#development-setup)
- [Branching Strategy](#branching-strategy)
- [Commit Conventions](#commit-conventions)
- [Pull Request Process](#pull-request-process)
- [Code Standards](#code-standards)

---

## Development Setup

```bash
# 1. Clone the repository
git clone <repo-url> && cd GreenMindDB

# 2. Copy environment config
cp .env.example .env

# 3. Start the dev stack
make dev

# 4. Verify everything is running
make health
```

See the [README](README.md) for detailed setup instructions.

---

## Branching Strategy

We use a **branch-based workflow** with two long-lived branches:

| Branch    | Purpose                                      |
|-----------|----------------------------------------------|
| `main`    | Stable, production-ready. No direct commits. |
| `develop` | Integration branch for ongoing work.         |

### Branch Types

Create branches from `develop` using these naming conventions:

| Prefix      | Purpose              | Example                      |
|-------------|----------------------|------------------------------|
| `feature/`  | New functionality     | `feature/live-sensor-stream` |
| `fix/`      | Bug fix               | `fix/api-validation`         |
| `hotfix/`   | Urgent production fix | `hotfix/login-crash`         |
| `refactor/` | Code improvements     | `refactor/backend-services`  |
| `docs/`     | Documentation only    | `docs/readme-update`         |
| `chore/`    | Maintenance / tooling | `chore/update-dependencies`  |

### Workflow

```bash
# 1. Start from develop
git checkout develop
git pull origin develop

# 2. Create your branch
git checkout -b feature/my-feature

# 3. Make changes, commit (see Commit Conventions below)
git add .
git commit -m "feat: add sensor streaming endpoint"

# 4. Push and create a PR
git push -u origin feature/my-feature
# → Open PR targeting 'develop' on GitHub
```

### Rules

- ✅ All changes go through Pull Requests
- ✅ PRs target `develop` (never `main` directly)
- ✅ `main` is only updated via release merges from `develop`
- ✅ CI must pass before merging
- ✅ At least one code review approval required

---

## Commit Conventions

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>

[optional body]
[optional footer]
```

### Types

| Type       | Description                          |
|------------|--------------------------------------|
| `feat`     | New feature                          |
| `fix`      | Bug fix                              |
| `docs`     | Documentation changes                |
| `style`    | Formatting (no logic changes)        |
| `refactor` | Code restructuring (no new features) |
| `test`     | Adding or updating tests             |
| `chore`    | Build, CI, or tooling changes        |

### Examples

```
feat(api): add greenhouse overview endpoint
fix(frontend): correct chart timezone offset
docs: update setup instructions for macOS
chore(ci): add Docker build step to pipeline
```

---

## Pull Request Process

1. **Create your branch** from `develop`
2. **Make your changes** and commit following conventions
3. **Run checks locally** before pushing:
   ```bash
   make lint
   make test
   ```
4. **Push and open a PR** targeting `develop`
5. **Fill out the PR template** completely
6. **Wait for CI** to pass (green checks)
7. **Request review** from at least one team member
8. **Address feedback** and push fixes
9. **Merge** once approved and CI is green

---

## Code Standards

### Backend (Python)

- **Formatter**: `black` (line-length 100)
- **Linter**: `ruff`
- **Tests**: `pytest`

```bash
# Format code
make format

# Lint
make lint

# Run tests
make test
```

### Frontend (TypeScript / Next.js)

- **Linter**: `eslint` via `next lint`
- **Formatter**: `prettier`

```bash
cd frontend
npm run lint
npm run format
```

### General Principles

- Small, focused functions
- No hardcoded secrets or configuration
- Use `.env` for all environment-specific values
- Write meaningful commit messages
- Add tests for new features and bug fixes
- Keep dependencies up to date
