# MyH local AI diagnostics

The inference service is `myh-ai.service`: llama.cpp `b10373` with the official Apache-2.0 Qwen2.5 0.5B Instruct Q4_K_M model. It listens only on `127.0.0.1:11435`, has no public Web UI, and is constrained by systemd to 900 MB RAM, one CPU, and 64 tasks.

The panel is the only client. It constructs a small, server-derived diagnostic context after checking authentication, tenant assignment, and `health.view`. Logs are sanitized and truncated before inference. The model receives no credentials, raw files, database access, Docker socket, shell, network tool, or mutation capability. Model output is advice, never authorization or an executable action.

The panel limits prompt length, output tokens, request duration, per-user request rate, and daily usage. Every request records the user, site, request type, status, latency, and token counters without storing the full prompt or answer.
