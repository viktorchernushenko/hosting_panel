# MyH UI inventory

Audit date: 2026-08-12. The inventory maps the existing Flask/Jinja interface; it does not infer unsupported functionality.

## Route map

| Route group | Pages | Role | Main action | Source of truth | Finding |
|---|---|---|---|---|---|
| `/`, `/technologies`, `/login` | Marketing, runtimes, authentication | Public | Sign in / get started | runtime registry, auth | Working; public and product shells are separate |
| `/dashboard` | User overview | User | Create site | Site, DatabaseResource, backups, AuditLog | Real values; server metrics are admin-only |
| `/sites`, `/sites/create`, `/site/<folder>` | Sites, wizard, site workspace | Assigned user | Create/manage site | Site, runtime health, ApplicationAccess | Central workflow exists; workspace anchors and hierarchy needed repair |
| `/domains` | All domains | Assigned user | Inspect/configure domain | live DNS/TLS probe + Site | Real checks; domain and TLS already presented together |
| `/databases` | All databases | Assigned user | Create/manage database | DatabaseResource + live MySQL size | Working; secrets remain write-only |
| `/dashboard/sftp-access` | File access | User | Enable/reset SFTP | SftpAccount + provisioner | Working; global label is File access |
| `/dashboard/deploy`, `/dashboard/deploy/history` | Deployments | Assigned user | Deploy/review | Integration + DeploymentEvent | Two pages for one workflow; history remains reachable from Deployments |
| `/backups`, `/logs`, `/profile` | Operations/account | User | Restore/read/update | filesystem backups, scoped logs, User | Working; log hints are deterministic and restore/delete require confirmation |
| `/developer/*` | Administration | Admin/developer by permission | Operate platform | services, Docker, jobs, audit tables | Hidden from regular users; technical detail is appropriate here |
| `/api/*` | Async/status integrations | Auth/permission specific | Read or mutate | backend services | No standalone UI required for health, metrics, capabilities and webhook endpoints |

## Component inventory

- Shell: responsive sidebar, sticky compact topbar, mobile drawer, desktop collapse.
- Navigation: Overview, Hosting, Operations, Account and admin-only group.
- Content: page headers, metric tiles, panels, compact tables, workspace tabs.
- Forms: standard fields, progressive `details`, runtime cards, destructive forms.
- Feedback: Flask flash messages, toast region, native dialog confirmation, scoped empty states.
- Data views: site/domain/database tables, backup lists, log viewer, admin tables.
- Global dialogs: version, privacy, destructive confirmation; closed backdrop is inert and aria-hidden.
- Settings scopes: Account settings, Site settings and Platform modules use distinct labels.

## Verified issues and dependency decisions

| Finding | Evidence | Decision |
|---|---|---|
| Duplicate authenticated header controls | `base.html` rendered a second `.global-header`, hidden only by CSS | Do not render it for authenticated users |
| Duplicate Deployments/Activity navigation | Both links addressed one DeploymentEvent workflow | Keep history route, remove duplicate sidebar entry, link it from Deployments/activity contexts |
| Broken workspace anchors | `#domain` labelled upload and `#backups` labelled mkdir | Correct semantic section IDs |
| Native destructive prompts | Site backup/file forms used inline `confirm()` | Route through shared accessible ConfirmDialog |
| Sites filter incomplete | Runtime only | Add status filter; retain simple client filtering |
| Last deployment absent from Sites table | DeploymentEvent exists | Map latest event by site name without changing schema |
| Site status vocabulary inconsistent | raw `error`, `configured`, `not deployed` | Translate to user-facing status while retaining technical detail in admin views |
| Very large inline legacy CSS | `base.html` includes legacy definitions overridden by `panel.css` | Preserve during this migration; removal requires visual regression coverage |

No fake buttons or placeholder controls were intentionally added. API-only routes are not considered routes without UI.
