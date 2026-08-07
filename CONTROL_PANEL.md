# Control Panel

## Location

- Source: `/home/myserver/hosting_panel`
- Main app: [`app.py`](app.py)
- Templates: [`templates/`](templates)
- Deployment: systemd service managed by `myh-guru.service`
- Repo: `git@github.com:viktorchernushenko/hosting_panel.git`
- Current branch: `main`
- Current HEAD: `fdeec22` (`admin + health-aware watchdog`)

## Runtime Stack

- Flask 3.0.3
- Flask-SQLAlchemy 3.1.1
- psutil 6.0.0
- gunicorn 23.0.0
- SQLite database stored in `instance/hosting.db`

## Current Roles

- `user`
- `developer` / `admin` flag via `is_admin`

## Existing Capabilities

- Login / logout
- User and site management
- Static site management
- File manager for assigned sites
- Site backups and restores
- Custom domains
- Git and ZIP deployment flows
- Deployment history
- Cloudflare DNS / SSL actions
- Platform metrics and health endpoints
- Docker container control
- System/service diagnostics
- Notifications
- Plugin marketplace / module registry

## Newly Added Audit Surface

- `GET /developer/system-audit`
- Read-only host / Docker / service readiness view
- Quick access from the developer dashboard

## Known Gaps

- RBAC is still coarse; permissions are not granular yet.
- There is no centralized application registry yet.
- There is no shared proxy network orchestration yet.
- Backup registry and restore safety workflow need normalization.
- Docker socket access strategy still needs review against least privilege.

## Design Constraint

- Keep the current design system and extend it in place.
- Do not replace the panel with another product.
- Do not duplicate existing sections if the function already exists.
