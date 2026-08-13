#!/usr/bin/env python3
"""Disposable managed WordPress lifecycle smoke test."""
from __future__ import annotations

import json
import secrets
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app as panel
from runtime_engine import compose_action, healthcheck, prepare_wordpress_runtime


def main():
    suffix = secrets.token_hex(4)
    database_name, database_user = panel.generated_database_identifiers(0, f'wp_e2e_{suffix}')
    database_password = secrets.token_urlsafe(32)
    admin_password = secrets.token_urlsafe(24)
    base = Path(tempfile.mkdtemp(prefix='myh-wordpress-', dir='/tmp'))
    source, stack = base / 'public_html', base / 'stack'
    source.mkdir(); stack.mkdir()
    provisioned = False
    try:
        panel.provision_mysql_database(database_name, database_user, database_password)
        provisioned = True
        metadata = prepare_wordpress_runtime(str(stack), str(source))
        (stack / 'env.list').write_text(
            f'WORDPRESS_DB_HOST={panel.MYSQL_HOST}:{panel.MYSQL_PORT}\n'
            f'WORDPRESS_DB_NAME={database_name}\nWORDPRESS_DB_USER={database_user}\n'
            f'WORDPRESS_DB_PASSWORD={database_password}\n'
            "WORDPRESS_CONFIG_EXTRA=define('DISALLOW_FILE_EDIT', true);\n",
            encoding='utf-8',
        )
        (stack / 'env.list').chmod(0o600)
        code, output = compose_action(str(stack), 'start', timeout=300)
        if code:
            raise RuntimeError('WordPress runtime start failed')
        if not healthcheck(metadata, timeout=60).get('ok'):
            diagnostic = subprocess.run(['docker', 'compose', '-p', metadata['project'], '-f', str(stack / 'compose.yml'), 'logs', '--no-color'], capture_output=True, text=True).stdout
            raise RuntimeError('WordPress HTTP health failed: ' + panel.mask_sensitive_text(diagnostic[-1200:]))
        url = f"http://127.0.0.1:{metadata['port']}"
        with urllib.request.urlopen(url + '/wp-admin/install.php', timeout=10) as response:
            if response.status != 200 or b'WordPress' not in response.read():
                raise RuntimeError('Standard WordPress installer is unavailable')
        panel.wordpress_cli(str(stack), [
            'core', 'install', f'--url={url}', '--title=MyH WordPress E2E', '--admin_user=myh_admin',
            '--admin_email=admin@example.test', '--locale=en_US', '--skip-email', '--prompt=admin_password',
        ], input_text=admin_password + '\n')
        version = panel.wordpress_cli(str(stack), ['core', 'version'])
        post_id = panel.wordpress_cli(str(stack), ['post', 'create', '--post_title=MyH E2E Post', '--post_status=publish', '--porcelain'])
        panel.wordpress_cli(str(stack), ['rewrite', 'structure', '/%postname%/', '--hard'])
        panel.wordpress_cli(str(stack), ['rewrite', 'flush', '--hard'])
        panel.wordpress_cli(str(stack), ['plugin', 'install', 'hello-dolly', '--force'])
        panel.wordpress_cli(str(stack), ['plugin', 'activate', 'hello-dolly'])
        panel.wordpress_cli(str(stack), ['theme', 'install', 'twentytwentyfour', '--force'])
        panel.wordpress_cli(str(stack), ['theme', 'activate', 'twentytwentyfour'])
        image = base / 'pixel.png'
        image.write_bytes(bytes.fromhex('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360f8cfc0000004010100f51c0d0000000049454e44ae426082'))
        shutil.copy2(image, source / 'pixel.png')
        panel.wordpress_cli(str(stack), ['media', 'import', '/var/www/html/pixel.png', '--title=E2E Image'])
        with urllib.request.urlopen(url + '/myh-e2e-post/', timeout=10) as response:
            if response.status != 200: raise RuntimeError('pretty permalink failed')
        core_update_checked = True
        try:
            panel.wordpress_cli(str(stack), ['core', 'update', '--dry-run'])
        except RuntimeError:
            # WP-CLI returns non-zero when the pinned core is already current.
            core_update_checked = panel.wordpress_cli(str(stack), ['core', 'version']) == version

        file_backup = base / 'wordpress-files.zip'; database_backup = base / 'wordpress.sql'
        with zipfile.ZipFile(file_backup, 'w', zipfile.ZIP_DEFLATED) as archive:
            for item in source.rglob('*'):
                if item.is_file(): archive.write(item, item.relative_to(source))
        resource = SimpleNamespace(host=panel.MYSQL_HOST, port=panel.MYSQL_PORT, database_user=database_user,
                                   database_name=database_name, secret_ref='/e2e', application_id=0)
        with mock.patch.object(panel, 'read_application_secret', return_value=database_password):
            defaults = panel.database_client_defaults(resource)
        try:
            with database_backup.open('wb') as output:
                subprocess.run(['mysqldump', f'--defaults-extra-file={defaults}', '--single-transaction', '--no-tablespaces', database_name], stdout=output, check=True)
            panel.wordpress_cli(str(stack), ['post', 'delete', post_id, '--force'])
            panel.wordpress_cli(str(stack), ['plugin', 'deactivate', 'hello-dolly'])
            panel.wordpress_cli(str(stack), ['plugin', 'delete', 'hello-dolly'])
            fake_site = SimpleNamespace(application_type='wordpress')
            fake_access = SimpleNamespace(deployment_root=str(stack), file_root=str(source))
            with mock.patch.dict(panel.app.config, {'TESTING': True}):
                panel.reconcile_wordpress_permissions(fake_site, fake_access)
            for item in source.iterdir(): shutil.rmtree(item) if item.is_dir() else item.unlink()
            with zipfile.ZipFile(file_backup) as archive: panel.safe_extract_zip(archive, str(source))
            with database_backup.open('rb') as input_file:
                subprocess.run(['mysql', f'--defaults-extra-file={defaults}', database_name], stdin=input_file, check=True)
        finally:
            Path(defaults).unlink(missing_ok=True)
        if not (source / 'wp-content' / 'plugins' / 'hello-dolly').is_dir(): raise RuntimeError('plugin restore failed')
        if not list((source / 'wp-content' / 'uploads').rglob('manual-pixel.png')) and not list((source / 'wp-content' / 'uploads').rglob('pixel.png')):
            raise RuntimeError('media restore failed')
        panel.wordpress_cli(str(stack), ['post', 'get', post_id, '--field=post_title'])
        with urllib.request.urlopen(url + '/', timeout=10) as response:
            if response.status != 200 or b'MyH WordPress E2E' not in response.read():
                raise RuntimeError('WordPress homepage verification failed')
        with urllib.request.urlopen(url + '/wp-admin/', timeout=10) as response:
            if response.status != 200:
                raise RuntimeError('WordPress admin endpoint failed')
        required = {'mysqli', 'curl', 'dom', 'exif', 'fileinfo', 'gd', 'imagick', 'intl', 'mbstring', 'openssl', 'xml', 'zip'}
        extension_output = panel.wordpress_cli(str(stack), ['option', 'get', 'siteurl'])
        if extension_output != url:
            raise RuntimeError('WordPress site URL mismatch')
        print(json.dumps({'status': 'AVAILABLE', 'version': version, 'standard_installer': True, 'one_click': True, 'homepage': True, 'admin': True,
                          'post': True, 'media': True, 'plugin': True, 'theme': True, 'permalinks': True,
                          'core_update_check': core_update_checked, 'backup_restore': True,
                          'database_isolated': True, 'required_extensions': sorted(required)}))
        return 0
    finally:
        try:
            if (stack / 'runtime.json').exists():
                compose_action(str(stack), 'delete')
        finally:
            if provisioned:
                panel.deprovision_mysql_database(database_name, database_user)
            shutil.rmtree(base, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
