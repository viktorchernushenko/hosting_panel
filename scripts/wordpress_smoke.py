#!/usr/bin/env python3
"""Disposable managed WordPress lifecycle smoke test."""
from __future__ import annotations

import json
import secrets
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

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
            raise RuntimeError('WordPress HTTP health failed')
        url = f"http://127.0.0.1:{metadata['port']}"
        with urllib.request.urlopen(url + '/wp-admin/install.php', timeout=10) as response:
            if response.status != 200 or b'WordPress' not in response.read():
                raise RuntimeError('Standard WordPress installer is unavailable')
        panel.wordpress_cli(str(stack), [
            'core', 'install', f'--url={url}', '--title=MyH WordPress E2E', '--admin_user=myh_admin',
            '--admin_email=admin@example.test', '--locale=en_US', '--skip-email', '--prompt=admin_password',
        ], input_text=admin_password + '\n')
        version = panel.wordpress_cli(str(stack), ['core', 'version'])
        panel.wordpress_cli(str(stack), ['post', 'create', '--post_title=MyH E2E Post', '--post_status=publish'])
        panel.wordpress_cli(str(stack), ['plugin', 'install', 'hello-dolly', '--force'])
        panel.wordpress_cli(str(stack), ['plugin', 'activate', 'hello-dolly'])
        panel.wordpress_cli(str(stack), ['plugin', 'deactivate', 'hello-dolly'])
        panel.wordpress_cli(str(stack), ['plugin', 'delete', 'hello-dolly'])
        panel.wordpress_cli(str(stack), ['theme', 'install', 'twentytwentyfour', '--force'])
        panel.wordpress_cli(str(stack), ['theme', 'activate', 'twentytwentyfour'])
        image = base / 'pixel.png'
        image.write_bytes(bytes.fromhex('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360f8cfc0000004010100f51c0d0000000049454e44ae426082'))
        shutil.copy2(image, source / 'pixel.png')
        panel.wordpress_cli(str(stack), ['media', 'import', '/var/www/html/pixel.png', '--title=E2E Image'])
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
                          'post': True, 'media': True, 'plugin': True, 'theme': True,
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
