# MyH product UI specification

## Product model

`Site` is the workspace. Global Domains, Databases, Deployments and Backups are cross-site indexes over the same resources, not separate concepts.

## Page anatomy

Authenticated pages use `AppShell > Sidebar + Main(Topbar + Content)`. Content uses a compact page header, optional metrics, then task-oriented sections. One primary action is exposed per page.

## Navigation

- Overview: Dashboard.
- Hosting: Sites, Domains, Databases, File access.
- Operations: Deployments, Backups, Logs.
- Account: Account settings.
- Administration (admin only): Overview, Users, Sites, Infrastructure/Server, Runtimes, Containers, Backup, Notifications, SFTP, SSL/Domains, Security/Audit, AI status and Platform modules.

## Layout and density

- Content maximum: 1440px; page padding: 24px desktop, 16px tablet, 12px mobile.
- Grid gaps use 12/16/20/24px tokens.
- Dashboard is comfortable, tables compact, forms medium, logs dense.
- Desktop sidebar is 244px and collapses to 72px. Below 900px it becomes a drawer.
- Tables scroll within a bounded wrapper; core mobile actions remain at least 40px high.

## Typography and surfaces

- Page title: 24–28px; section title: 16–20px; body: 14–16px; metadata: 12–14px.
- Dark surfaces are `#080b14`, `#101521`, and `#161c2a`, separated primarily by 1px borders.
- Radius uses 6/10/14/18px. Shadows are reserved for overlays.

## Components and behavior

Buttons use Primary, Secondary/Ghost, and Danger hierarchy. Status always uses text plus color. Forms have visible labels and help text. Empty states explain the next action. Shared toast, ConfirmDialog, CopyButton and runtime cards are reused without adding a UI framework.

Async success is shown only after a backend response. Destructive actions require the shared dialog. Motion is 160–180ms and disabled through `prefers-reduced-motion`.

## Data mapping

| UI datum | Source |
|---|---|
| Site status | runtime health probe + Site state |
| Runtime availability | verified runtime registry |
| Domain/DNS/HTTPS | live `probe_domain_status` result |
| Database status/size | DatabaseResource + MySQL query |
| Last deployment | latest DeploymentEvent for the site |
| Storage | filesystem quota snapshot |
| Backup count/files | scoped backup directory |
| Recent activity | scoped AuditLog |

No display value may be invented when a source is unavailable; the UI uses `Not verified` or an explanatory empty/error state.
