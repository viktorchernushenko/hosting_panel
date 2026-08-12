# MySQL hosting

MySQL 8.4 is enabled as `mysql.service`, bound to the private Docker bridge at `172.23.0.1:3306`. Root uses local socket authentication. The panel reads its restricted provisioner credentials from `/etc/mysql/myh-provisioner.cnf` (mode 0600) and invokes narrowly scoped stored procedures.

Every application receives a unique database and least-privilege database account. Containers connect through `hosting-databases`; bridge ICC is disabled to prevent lateral container traffic. Logical backups are written below `/srv/backups/mysql/<owner>/<resource>` and restore operations are audited by the panel.
