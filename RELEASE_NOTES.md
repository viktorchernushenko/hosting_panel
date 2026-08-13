# MyH 2.8.2 release notes

Released: 2026-08-13

MyH 2.8.2 is an urgent WordPress workspace bugfix. The backend now always supplies a complete `wordpress_state` structure, including non-WordPress requests, while the template also uses defensive dictionary access.

Runtime status now combines the site record, exact Compose project label, container state, HTTP readiness and deterministic WordPress installation detection. An unrelated container can no longer make a site appear running merely because it uses a WordPress image.

WordPress sites without files, a deployment stack or database display `Needs setup`; public and WordPress Admin actions remain unavailable until the site is actually ready.
