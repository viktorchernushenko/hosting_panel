# TLS architecture

The browser connects to a Cloudflare-managed edge certificate. Cloudflare Tunnel forwards to `http://127.0.0.1:5000`; there is no public origin listener or local origin certificate in this path.

```text
Browser -- HTTPS / Cloudflare edge certificate --> Cloudflare
Cloudflare Tunnel -- encrypted tunnel / loopback HTTP --> MyH Gunicorn
```

Local `certbot renew` is neither required nor a valid edge-renewal test. Admin → DNS & SSL reports live expiry and `Managed by Cloudflare`. The watchdog validates the browser-facing certificate at a 30-day threshold. Cloudflare SSL mode is displayed from its API and is not changed automatically.
