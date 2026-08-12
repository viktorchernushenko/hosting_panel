# MyH runtime architecture

`runtime_registry.py` is the user-visible source of truth. A runtime is `AVAILABLE` only when its configured image or engine exists, it is enabled by the administrator, and `instance/runtime_health.json` contains a successful full lifecycle result. Merely having an image or a running process is not sufficient.

The administrator can run the same verification from **Admin → Runtimes → Run full verification**. The underlying reproducible command is:

```bash
PYTHONPATH=. python3 scripts/runtime_smoke.py --output instance/runtime_health.json
```

The verification creates isolated temporary projects under `/tmp`, exercises create/build/start/HTTP/health/logs/restart/stop/second-start/delete, inspects resource and namespace restrictions, and cleans up containers and networks in `finally`.

`scripts/php_mysql_smoke.py` additionally provisions temporary isolated MySQL credentials, verifies a real PDO connection and table write from the PHP container, then removes both the Compose project and database/user in `finally`.

## Production matrix

| Runtime | Version/template | Create | Build | Start | HTTP | Health | Restart | Stop/start | Logs | Delete | Security | Status |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Static | nginx 1.27 Alpine | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | AVAILABLE |
| PHP | 8.2 FPM + nginx | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | AVAILABLE |
| Node.js | 22 Alpine | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | AVAILABLE |
| Python | 3.12 Alpine + Gunicorn 23 | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | AVAILABLE |
| Docker | validated Dockerfile | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | AVAILABLE |

Latest recorded registry verification on this host: `2026-08-12T16:01:54+0300`; the same full lifecycle matrix was rerun successfully during the 2.2.0 release audit at 17:05 local time. The live admin page and API read the machine-local report rather than this documentation.

PHP 8.2 includes the production baseline: PDO/MySQL, mysqli, mbstring, curl, OpenSSL, fileinfo, JSON, XML, ZIP, GD and intl. PHP 8.3/8.4 remain usable only by already pinned applications; new sites use the verified 8.2 release until separate version-specific E2E is recorded.

Static SPA fallback is opt-in. Static nginx also provides gzip, asset cache headers and custom `404.html` handling. Node/Python commands execute only in their application container and must bind to port 8080. Runtime ports bind to loopback and are reached through the shared MyH domain/TLS layer.

Customer Docker Compose is disabled for the first release because a multi-service policy is not yet sufficiently constrained. Dockerfile deployments reject public port control and run with a read-only root filesystem, loopback-only published port, CPU/memory/PID limits, dropped capabilities and `no-new-privileges`; no Docker socket, host namespace, host device or host filesystem mount is provided.

Framework names are recommendations, not runtimes. React/Vue/Angular/Svelte builds use Static; Laravel and WordPress use PHP plus optional MySQL; Django/Flask/FastAPI use Python; Go/Java/.NET/Ruby use validated Docker. Framework compatibility depends on project configuration.
