# MyH 2.9.2 release notes

MyH 2.9.2 fixes compressed response corruption in the wildcard customer-site proxy. The proxy now follows a consistent raw-byte strategy: encoded upstream bytes retain their matching `Content-Encoding`, while stale `Content-Length` and HTTP hop-by-hop headers are removed.

WordPress redirects are returned to the browser instead of being followed internally through Cloudflare. As a result, `/wp-admin/` correctly redirects to the canonical public `/wp-login.php` URL and the browser receives normal HTML rather than compressed bytes without encoding metadata.
