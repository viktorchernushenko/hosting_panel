# Application runtimes

Supported profiles: Static (`nginx:1.27-alpine`), PHP 8.2/8.3/8.4 (platform images with `mysqli` and `pdo_mysql`), Node.js 22, Python 3.12, controlled Dockerfile/Compose, and WordPress.

Managed containers bind only to `127.0.0.1`, restart unless stopped, drop capabilities, enable `no-new-privileges`, and have CPU, memory, PID, and log rotation limits. Custom Docker definitions cannot publish ports, use host namespaces, privileged mode, devices, Docker socket, or bind mounts outside their application. The entry service must expose port 8080.
