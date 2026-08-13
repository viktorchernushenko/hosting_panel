# MyH 2.10.1 release notes

MyH 2.10.1 is a focused WordPress installation-mode fix. The create form, POST route, Site model, stack metadata and provisioner continue to use one canonical `automatic`/`manual` value. An automatic failure now remains automatic and is shown as an automatic installation failure rather than a manual setup workflow.

Automatic retry accepts fresh WordPress administrator credentials, reconciles the existing runtime/database without duplicates, and reruns the WP-CLI installation. Successful automatic sites continue directly to installed/ready and never show the setup checklist.

Production validation created Auto A, Manual B and Auto C through the normal `/sites/create` form route. A/C installed WordPress with isolated databases and valid HTTPS homepage/admin/login responses; B retained an empty root, no database and `needs_setup`. All disposable resources were removed through the normal site deletion flow.
