#!/usr/bin/env python3
"""Verify an isolated PHP runtime can use a provisioned MyH MySQL database."""
from __future__ import annotations

import secrets
import shutil
import tempfile
import urllib.request
from pathlib import Path

import app as panel
from runtime_engine import compose_action, healthcheck, prepare_runtime


def main() -> int:
    suffix = secrets.token_hex(4)
    database_name, database_user = panel.generated_database_identifiers(0, "runtime_e2e")
    database_password = secrets.token_urlsafe(24)
    base = Path(tempfile.mkdtemp(prefix="myh-php-mysql-", dir="/tmp"))
    source, stack = base / "source", base / f"stack-{suffix}"
    source.mkdir(parents=True)
    provisioned = False
    try:
        panel.provision_mysql_database(database_name, database_user, database_password)
        provisioned = True
        (source / "index.php").write_text(
            '<?php try {$pdo=new PDO("mysql:host=".getenv("DB_HOST").";port=".getenv("DB_PORT").";dbname=".getenv("DB_NAME"),getenv("DB_USER"),getenv("DB_PASSWORD"),[PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION]); $pdo->exec("CREATE TABLE IF NOT EXISTS runtime_probe (id INT PRIMARY KEY)"); echo "PHP PDO MYSQL E2E OK";} catch(Throwable $e){http_response_code(500); echo "DB ERROR";}',
            encoding="utf-8",
        )
        metadata = prepare_runtime(str(stack), str(source), "php", "8.2")
        (stack / "env.list").write_text(
            f"DB_HOST={panel.MYSQL_HOST}\nDB_PORT={panel.MYSQL_PORT}\nDB_NAME={database_name}\nDB_USER={database_user}\nDB_PASSWORD={database_password}\n",
            encoding="utf-8",
        )
        (stack / "env.list").chmod(0o600)
        code, output = compose_action(str(stack), "start", timeout=300)
        if code:
            raise RuntimeError(output[-500:])
        ready = healthcheck(metadata, timeout=30)
        if not ready.get("ok"):
            raise RuntimeError(f"healthcheck failed: {ready}")
        with urllib.request.urlopen(f'http://127.0.0.1:{metadata["port"]}/', timeout=5) as response:
            body = response.read().decode("utf-8", errors="replace")
        if body != "PHP PDO MYSQL E2E OK":
            raise RuntimeError("unexpected PHP/MySQL response")
        print("PHP PDO MYSQL E2E: PASS")
        return 0
    finally:
        try:
            if (stack / "runtime.json").exists():
                compose_action(str(stack), "delete")
        finally:
            if provisioned:
                panel.deprovision_mysql_database(database_name, database_user)
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
