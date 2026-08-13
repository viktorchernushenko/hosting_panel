# MyH product audit

Audit checkpoint: release `2.4.0`. Pre-change recovery set: `/srv/backups/platform-audit/20260812-143254` (root-only, SHA-256 verified).

## Control reality map

| Control | Page | Backend | Result | Status |
|---|---|---|---|---|
| Sign in / sign out | `/login`, global header | session + password hash + CSRF | authenticated session lifecycle | WORKING |
| Create site | `/sites/create` | scaffold + controlled runtime | persisted site and stack | WORKING |
| Start/stop/restart/delete runtime | Site workspace | Docker Compose lifecycle | Docker state + health refresh | WORKING |
| Upload/edit/rename/move/copy/delete/download | Site workspace | canonical scoped paths | site-root filesystem mutation | WORKING |
| Add domain | Site workspace | Cloudflare record operation | persisted only after provider success | WORKING |
| DNS/TLS check | `/domains` | resolver + verified TLS handshake | live DNS, certificate state and expiry | WORKING |
| Create/reset/delete database | `/databases` | restricted MySQL provisioner | isolated MySQL resource | WORKING |
| Database backup/download/restore/delete | `/databases` | logical SQL dump/import | resource-scoped backup lifecycle | WORKING |
| SFTP enable/reset | `/dashboard/sftp-access` | queued OpenSSH provisioner | chrooted internal-sftp account | WORKING |
| Site backup/download/restore/delete | Site workspace, `/backups` | ZIP lifecycle + safe extraction | tested restore point | WORKING |
| Git/GitHub deploy | Deployments | validated repository + scoped secret | deployment event and logs | WORKING |
| Runtime logs | Site workspace, `/logs` | scoped Docker/deploy logs | secrets redacted before response | WORKING |
| Admin service/container actions | Admin pages | admin-only allowlisted actions | real systemd/Docker result | WORKING |
| Runtime test buttons | Create wizard / PHP health | CSRF API request | actual runtime inspection | WORKING |
| Public CPU/RAM controls | Legacy admin dashboard | removed | no guest infrastructure disclosure | DEAD / REMOVED |
| Password reset email | Login | no mail/reset backend | link intentionally absent | NOT IMPLEMENTED |
| Self-registration | Login | invite-only policy | policy stated, no fake control | NOT IMPLEMENTED |
| Runtime guidance | Create Site | deterministic project marker detection | recommendation requiring confirmation | WORKING |
| Log guidance | `/logs` | fixed common-error mapping over sanitized logs | direct next-step hints | WORKING |

## Route and ownership findings

Public routes are limited to homepage, login, health/status metadata, static assets, webhooks protected by configured secrets, and hosted site content. Hosting resource routes require authentication and application assignment plus a named permission. Administrative infrastructure, users, Docker, global logs, DNS control plane, modules, and system audit are protected by admin/developer decorators and server-side checks.

User A/User B regression covers foreign site status, database detail, file workspace, backup download, infrastructure, and Docker APIs. Expected and observed result is HTTP 403 or non-enumerating 404.

## Verification limits

No Chrome DevTools MCP is available in this environment, so real-browser console, Network panel, Lighthouse scores, and viewport screenshots remain **NOT VERIFIED**. Responsive CSS breakpoints and keyboard/focus semantics were inspected and unit/server tests pass, but they are not a substitute for browser evidence.
