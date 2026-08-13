#!/usr/bin/env python3
"""Disposable full-manual and assisted-manual WordPress browser-flow E2E."""
from __future__ import annotations

import http.cookiejar
import io
import json
import secrets
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app as panel
from runtime_engine import compose_action, healthcheck, prepare_wordpress_runtime


def request(opener, url, data=None):
    encoded = urllib.parse.urlencode(data).encode() if data is not None else None
    with opener.open(url, encoded, timeout=30) as response:
        return response.status, response.geturl(), response.read()


def run_flow(package, assisted):
    suffix = secrets.token_hex(4); base = Path(tempfile.mkdtemp(prefix='myh-wp-manual-', dir='/tmp'))
    source, stack = base / 'public_html', base / 'stack'; source.mkdir(); stack.mkdir()
    database_name, database_user = panel.generated_database_identifiers(0, f'wp_manual_{suffix}')
    database_password = secrets.token_urlsafe(32); admin_password = secrets.token_urlsafe(24)
    provisioned = False
    try:
        panel.provision_mysql_database(database_name, database_user, database_password); provisioned = True
        metadata = prepare_wordpress_runtime(str(stack), str(source), populate_wordpress=False)
        (stack / 'env.list').write_text('', encoding='utf-8'); (stack / 'env.list').chmod(0o600)
        code, _ = compose_action(str(stack), 'start', timeout=300)
        if code: raise RuntimeError('manual PHP runtime start failed')
        placeholder = source / 'index.php'; placeholder.write_text('<?php echo "PHP OK";', encoding='utf-8')
        if not healthcheck(metadata, timeout=60).get('ok'): raise RuntimeError('manual PHP health failed')
        placeholder.unlink()
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            prefix = panel.zip_single_root_folder(archive.infolist())
            panel.safe_extract_zip(archive, str(source), strip_prefix=prefix or '')
        url = f"http://127.0.0.1:{metadata['port']}"; opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        if assisted:
            resource = SimpleNamespace(database_name=database_name, database_user=database_user,
                host=panel.MYSQL_HOST, port=panel.MYSQL_PORT, secret_ref='/e2e', application_id=0)
            with mock.patch.object(panel, 'read_application_secret', return_value=database_password):
                (source / 'wp-config.php').write_text(panel.wordpress_config_contents(resource), encoding='utf-8')
            status, _, body = request(opener, url + '/wp-admin/install.php')
            if status != 200 or b'weblog_title' not in body: raise RuntimeError('assisted install screen unavailable')
        else:
            status, _, body = request(opener, url + '/wp-admin/setup-config.php?step=1')
            if status != 200 or b'name="dbname"' not in body: raise RuntimeError('setup-config screen unavailable')
            status, _, body = request(opener, url + '/wp-admin/setup-config.php?step=2', {
                'dbname': database_name, 'uname': database_user, 'pwd': database_password,
                'dbhost': f'{panel.MYSQL_HOST}:{panel.MYSQL_PORT}', 'prefix': 'wp_', 'language': 'en_US',
            })
            if status != 200 or not (source / 'wp-config.php').is_file(): raise RuntimeError('WordPress did not generate wp-config.php')
        status, _, body = request(opener, url + '/wp-admin/install.php?step=2', {
            'weblog_title': 'MyH Manual E2E', 'user_name': 'manual_admin', 'admin_password': admin_password,
            'admin_password2': admin_password, 'pw_weak': '1', 'admin_email': 'manual@example.test',
            'blog_public': '0', 'Submit': 'Install WordPress', 'language': 'en_US',
        })
        if status != 200 or b'Success' not in body: raise RuntimeError('manual browser installation failed')
        if panel.wordpress_cli(str(stack), ['core', 'is-installed']) != '': raise RuntimeError('manual core state failed')
        panel.wordpress_cli(str(stack), ['post', 'create', '--post_title=Manual E2E Post', '--post_status=publish'])
        image = source / 'manual-pixel.png'; image.write_bytes(bytes.fromhex('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360f8cfc0000004010100f51c0d0000000049454e44ae426082'))
        panel.wordpress_cli(str(stack), ['media', 'import', '/var/www/html/manual-pixel.png', '--title=Manual Image'])
        status, _, body = request(opener, url + '/')
        if status != 200 or b'MyH Manual E2E' not in body: raise RuntimeError('manual homepage failed')
        return {'mode': 'assisted_manual' if assisted else 'full_manual', 'setup_config': not assisted,
                'wp_config': True, 'install': True, 'admin': True, 'post': True, 'media': True}
    finally:
        try:
            if (stack / 'runtime.json').exists(): compose_action(str(stack), 'delete')
        finally:
            if provisioned: panel.deprovision_mysql_database(database_name, database_user)
            shutil.rmtree(base, ignore_errors=True)


def main():
    with urllib.request.urlopen('https://wordpress.org/latest.zip', timeout=60) as response: package = response.read()
    results = [run_flow(package, assisted=False), run_flow(package, assisted=True)]
    print(json.dumps({'status': 'AVAILABLE', 'results': results}))
    return 0


if __name__ == '__main__': raise SystemExit(main())
