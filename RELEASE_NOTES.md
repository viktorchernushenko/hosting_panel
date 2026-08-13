# MyH 2.4.0 release notes

Released: 2026-08-13

MyH 2.4.0 removes the low-usage inference integration and replaces its useful runtime and log assistance with deterministic diagnostics. Create Site detects project markers through the existing runtime registry, and Logs classifies a small set of known failures with direct next steps.

The hosting panel no longer exposes assistant pages, contextual inference controls, provider APIs, AI configuration, model credentials, port `11435`, or an inference service. Historical `ai.*` audit events are retained in the shared audit log; there was no dedicated AI table to migrate.

The production audit measured two lifetime runtime-recommendation events and no site/log diagnostic use. Removing the local model and service frees their measured disk and memory footprint without changing core hosting workflows.

See [CHANGELOG.md](CHANGELOG.md) for verified changes and [OPERATIONS.md](OPERATIONS.md) for production details.
