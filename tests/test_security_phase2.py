import io
import os
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault('HOSTING_PANEL_SECRET', 'test-secret-key-1234567890abcdef')
os.environ.setdefault('HOSTING_PANEL_DATABASE_URI', f"sqlite:////tmp/hosting-panel-tests-{os.getpid()}.db")

import app as panel_app


class SecurityPhase2Tests(unittest.TestCase):
    def setUp(self):
        self.app = panel_app.app
        self.app.config['TESTING'] = True
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        self.app_context = self.app.app_context()
        self.app_context.push()
        panel_app.db.session.remove()
        panel_app.db.engine.dispose()
        panel_app.db.drop_all()
        panel_app.db.create_all()

    def tearDown(self):
        panel_app.db.session.remove()
        panel_app.db.drop_all()
        self.app_context.pop()

    def _create_user(self, username, role='user', is_admin=False):
        user = panel_app.User(
            username=username,
            first_name=username.capitalize(),
            last_name='Tester',
            phone='0000000000',
            email=f'{username}@example.test',
            password=panel_app.generate_password_hash('StrongPass123!'),
            role=role,
            is_admin=is_admin,
        )
        panel_app.db.session.add(user)
        panel_app.db.session.commit()
        return user

    def _create_site(self, owner, name='demo'):
        site = panel_app.Site(
            name=name,
            folder_name=f'{owner.username}_{name}',
            user_id=owner.id,
        )
        panel_app.db.session.add(site)
        panel_app.db.session.commit()
        panel_app.ensure_application_access(site)
        return site

    def _auth_session(self, client, user_id, csrf_token='csrf-token'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id
            sess['username'] = 'test'
            sess['role'] = 'admin'
            sess['_csrf_token'] = csrf_token

    def test_api_post_requires_csrf_for_authenticated_user(self):
        admin = self._create_user('admin1', role='admin', is_admin=True)
        client = self.app.test_client()
        self._auth_session(client, admin.id)

        # No CSRF header -> blocked
        denied = client.post('/api/console', json={'command': 'uptime'})
        self.assertEqual(denied.status_code, 403)
        self.assertIn('CSRF', denied.get_data(as_text=True))

        # CSRF header present -> route executes and returns JSON payload
        allowed = client.post('/api/console', json={'command': 'invalid'}, headers={'X-CSRF-Token': 'csrf-token'})
        self.assertEqual(allowed.status_code, 400)
        payload = allowed.get_json()
        self.assertIsInstance(payload, dict)
        self.assertIn('success', payload)

    def test_sftp_provisioner_explicitly_disables_agent_forwarding(self):
        worker_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'systemd', 'myh-sftp-provision-worker.sh')
        with open(worker_path, encoding='utf-8') as handle:
            worker = handle.read()
        self.assertIn('lines.append("    DisableForwarding yes")', worker)
        self.assertIn('lines.append("    AllowTcpForwarding no")', worker)
        self.assertIn('lines.append("    AllowAgentForwarding no")', worker)

    def test_unassigned_developer_cannot_access_foreign_site_status(self):
        owner = self._create_user('owner1', role='user', is_admin=False)
        developer = self._create_user('dev1', role='developer', is_admin=False)
        site = self._create_site(owner, name='private')

        client = self.app.test_client()
        self._auth_session(client, developer.id)
        response = client.get(f'/api/sites/{site.id}/status')
        self.assertEqual(response.status_code, 403)

    def test_user_a_cannot_access_user_b_resources_or_admin(self):
        user_a = self._create_user('user-a')
        user_b = self._create_user('user-b')
        site_b = self._create_site(user_b, name='site-b')
        database_b = panel_app.DatabaseResource(
            application_id=site_b.id, display_name='db-b', database_name='tenant_b_db',
            database_user='tenant_b_user', secret_ref='/not/exposed', created_by=user_b.id,
        )
        panel_app.db.session.add(database_b)
        panel_app.db.session.commit()

        client = self.app.test_client()
        self._auth_session(client, user_a.id)
        self.assertEqual(client.get(f'/api/sites/{site_b.id}/status').status_code, 403)
        self.assertIn(client.get(f'/api/databases/{database_b.id}').status_code, {403, 404})
        self.assertEqual(client.get(f'/databases/{database_b.id}/studio').status_code, 404)
        self.assertEqual(client.get(f'/api/databases/{database_b.id}/tables').status_code, 404)
        self.assertEqual(client.get(f'/site/{site_b.folder_name}').status_code, 403)
        self.assertEqual(client.get(f'/site/{site_b.id}/backup/20260812-120000.zip/download').status_code, 403)
        self.assertEqual(client.get('/developer/infrastructure').status_code, 403)
        self.assertEqual(client.get('/api/docker/containers').status_code, 403)

    def test_database_studio_blocks_server_level_sql_before_connection(self):
        owner = self._create_user('studio-owner')
        site = self._create_site(owner, name='studio')
        resource = panel_app.DatabaseResource(
            application_id=site.id, display_name='studio', database_name='tenant_studio',
            database_user='tenant_studio_user', secret_ref='/not-used', created_by=owner.id,
        )
        panel_app.db.session.add(resource); panel_app.db.session.commit()
        client = self.app.test_client(); self._auth_session(client, owner.id)
        response = client.post(f'/api/databases/{resource.id}/sql', json={'sql': 'DROP DATABASE tenant_studio'},
                               headers={'X-CSRF-Token': 'csrf-token'})
        self.assertEqual(response.status_code, 403)

    def test_database_studio_identifier_quoting_and_type_allowlist(self):
        self.assertEqual(panel_app.quote_mysql_identifier('order-items'), '`order-items`')
        self.assertEqual(panel_app.quote_mysql_identifier('a`b'), '`a``b`')
        with self.assertRaises(ValueError): panel_app.quote_mysql_identifier('')
        self.assertTrue(panel_app.STUDIO_COLUMN_TYPE.fullmatch('VARCHAR(255)'))
        self.assertFalse(panel_app.STUDIO_COLUMN_TYPE.fullmatch('VARCHAR(20); DROP TABLE users'))

    def test_database_studio_page_renders_native_tabs(self):
        owner = self._create_user('studio-render')
        site = self._create_site(owner, name='studio-render')
        resource = panel_app.DatabaseResource(
            application_id=site.id, display_name='catalog', database_name='tenant_catalog',
            database_user='tenant_catalog_user', secret_ref='/mocked', created_by=owner.id,
        )
        panel_app.db.session.add(resource); panel_app.db.session.commit()
        connection = mock.MagicMock(); connection.__enter__.return_value = connection
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {'version': '8.4.6'}
        cursor.fetchall.return_value = [{'name': 'products', 'row_count': 2, 'size_bytes': 4096, 'engine': 'InnoDB', 'updated': None}]
        client = self.app.test_client(); self._auth_session(client, owner.id)
        with tempfile.TemporaryDirectory() as backup_dir, \
             mock.patch.object(panel_app, 'mysql_tenant_connection', return_value=connection), \
             mock.patch.object(panel_app, 'database_size_bytes', return_value=4096), \
             mock.patch.object(panel_app, 'database_backup_directory', return_value=backup_dir):
            response = client.get(f'/databases/{resource.id}/studio')
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        for label in ('Database Studio', 'Таблиці', 'Імпорт / Експорт', 'Резервні копії', 'Підключення'):
            self.assertIn(label, body)

    def test_database_page_localizes_uk_and_reports_backend_engine_state(self):
        owner = self._create_user('database-owner')
        self._create_site(owner, name='store')
        client = self.app.test_client()
        self._auth_session(client, owner.id)
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value.fetchone.return_value = {'version': '8.4.6-0ubuntu0'}
        with mock.patch.object(panel_app, 'mysql_provision_connection', return_value=connection):
            response = client.get('/databases')
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('Створюйте та керуйте базами даних', body)
        self.assertIn('MySQL 8.4.6', body)
        self.assertIn('PostgreSQL', body)
        self.assertIn('Тимчасово недоступна', body)

    def test_unavailable_database_engine_is_rejected_before_provisioning(self):
        owner = self._create_user('postgres-owner')
        site = self._create_site(owner, name='api')
        client = self.app.test_client()
        self._auth_session(client, owner.id)
        with mock.patch.object(panel_app, 'provision_mysql_database') as provision:
            response = client.post(f'/site/{site.id}/database', data={
                '_csrf_token': 'csrf-token', 'database_name': 'store', 'engine': 'postgresql',
            })
        self.assertEqual(response.status_code, 302)
        provision.assert_not_called()
        self.assertEqual(panel_app.DatabaseResource.query.count(), 0)

    def test_capabilities_are_backend_derived_and_require_login(self):
        client = self.app.test_client()
        self.assertEqual(client.get('/api/capabilities').status_code, 401)
        viewer = self._create_user('viewer-cap', role='viewer')
        self._auth_session(client, viewer.id)
        response = client.get('/api/capabilities')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(any(response.get_json()['capabilities'].values()))
        self.assertTrue(response.headers.get('X-Request-ID'))

    def test_login_accepts_email_and_keeps_session_out_of_url(self):
        user = self._create_user('email-login')
        client = self.app.test_client()
        client.get('/login')
        with client.session_transaction() as sess:
            csrf = sess['_csrf_token']
        response = client.post('/login', data={
            '_csrf_token': csrf, 'username': user.email.upper(), 'password': 'StrongPass123!',
        }, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('session', response.headers.get('Location', '').lower())

    @mock.patch.object(panel_app.subprocess, 'run')
    def test_runtime_logs_redact_secrets(self, mocked_run):
        owner = self._create_user('log-owner')
        site = self._create_site(owner, name='logs')
        access = panel_app.ensure_application_access(site)
        os.makedirs(access.deployment_root, exist_ok=True)
        with open(os.path.join(access.deployment_root, 'runtime.json'), 'w', encoding='utf-8') as handle:
            handle.write('{"project":"safe-project"}')
        mocked_run.return_value = mock.Mock(returncode=0, stdout='password=NeverShowMe\nAuthorization: Bearer secret-token', stderr='')
        client = self.app.test_client()
        self._auth_session(client, owner.id)
        response = client.get(f'/site/{site.id}/runtime/logs')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('NeverShowMe', response.get_data(as_text=True))
        self.assertNotIn('secret-token', response.get_data(as_text=True))

    def test_removed_ai_routes_are_not_registered(self):
        routes = {rule.rule for rule in self.app.url_map.iter_rules()}
        self.assertNotIn('/ai', routes)
        self.assertNotIn('/developer/ai', routes)
        self.assertFalse(any('/ai/' in route for route in routes))

    def test_log_issue_classification_is_deterministic_and_deduplicated(self):
        issues = panel_app.classify_log_issues([
            'npm ERR dependency install failed',
            'Error: EACCES permission denied',
            'again: permission denied',
        ])
        self.assertEqual([issue['code'] for issue in issues], ['dependencies', 'permissions'])

    def test_file_copy_move_download_and_escape_protection(self):
        owner = self._create_user('file-owner')
        site = self._create_site(owner, name='files')
        access = panel_app.ensure_application_access(site)
        os.makedirs(access.file_root, exist_ok=True)
        for stale in ('copies/copied.txt', 'moved.txt', 'escape-link'):
            stale_path = os.path.join(access.file_root, stale)
            if os.path.lexists(stale_path):
                os.unlink(stale_path)
        with open(os.path.join(access.file_root, 'source.txt'), 'w', encoding='utf-8') as handle:
            handle.write('tenant-file')
        client = self.app.test_client()
        self._auth_session(client, owner.id)
        copied = client.post(f'/site/{site.folder_name}', data={
            '_csrf_token': 'csrf-token', 'action': 'copy', 'file_path': 'source.txt',
            'destination': 'copies/copied.txt', 'current_dir': '',
        })
        self.assertEqual(copied.status_code, 302)
        moved = client.post(f'/site/{site.folder_name}', data={
            '_csrf_token': 'csrf-token', 'action': 'move', 'file_path': 'copies/copied.txt',
            'destination': 'moved.txt', 'current_dir': '',
        })
        self.assertEqual(moved.status_code, 302)
        downloaded = client.get(f'/site/{site.id}/files/download?path=moved.txt')
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.get_data(), b'tenant-file')
        self.assertIn(client.get(f'/site/{site.id}/files/download?path=../../etc/passwd').status_code, {400, 404})
        outside = tempfile.NamedTemporaryFile(delete=False)
        outside.write(b'outside-secret'); outside.close()
        link_path = os.path.join(access.file_root, 'escape-link')
        try:
            os.symlink(outside.name, link_path)
            self.assertIn(client.get(f'/site/{site.id}/files/download?path=escape-link').status_code, {400, 404})
        finally:
            os.unlink(outside.name)

    def test_site_backup_restore_round_trip(self):
        owner = self._create_user('backup-owner')
        site = self._create_site(owner, name='restore')
        access = panel_app.ensure_application_access(site)
        os.makedirs(access.file_root, exist_ok=True)
        target = os.path.join(access.file_root, 'state.txt')
        with open(target, 'w', encoding='utf-8') as handle:
            handle.write('before-backup')
        archive = panel_app.create_backup_archive(site, access=access)
        with open(target, 'w', encoding='utf-8') as handle:
            handle.write('after-backup')
        client = self.app.test_client()
        self._auth_session(client, owner.id)
        response = client.post(
            f'/site/{site.id}/backup/{os.path.basename(archive)}/restore',
            data={'_csrf_token': 'csrf-token'},
        )
        self.assertEqual(response.status_code, 302)
        with open(target, encoding='utf-8') as handle:
            self.assertEqual(handle.read(), 'before-backup')

    def test_safe_extract_zip_accepts_safe_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = os.path.join(tmp, 'safe.zip')
            dest = os.path.join(tmp, 'out')
            os.makedirs(dest, exist_ok=True)

            with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('assets/index.html', '<h1>ok</h1>')

            with zipfile.ZipFile(archive_path, 'r') as archive:
                panel_app.safe_extract_zip(archive, dest)

            self.assertTrue(os.path.exists(os.path.join(dest, 'assets', 'index.html')))

    def test_safe_extract_zip_blocks_zip_slip(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = os.path.join(tmp, 'zip-slip.zip')
            dest = os.path.join(tmp, 'out')
            os.makedirs(dest, exist_ok=True)

            with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('../../etc/passwd', 'bad')

            with zipfile.ZipFile(archive_path, 'r') as archive:
                with self.assertRaises(ValueError):
                    panel_app.safe_extract_zip(archive, dest)

    def test_safe_extract_zip_blocks_absolute_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = os.path.join(tmp, 'absolute-path.zip')
            dest = os.path.join(tmp, 'out')
            os.makedirs(dest, exist_ok=True)

            with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('/root/secret.txt', 'bad')

            with zipfile.ZipFile(archive_path, 'r') as archive:
                with self.assertRaises(ValueError):
                    panel_app.safe_extract_zip(archive, dest)

    def test_safe_extract_zip_blocks_symlink_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = os.path.join(tmp, 'symlink.zip')
            dest = os.path.join(tmp, 'out')
            os.makedirs(dest, exist_ok=True)

            with zipfile.ZipFile(archive_path, 'w') as archive:
                link = zipfile.ZipInfo('link-to-outside')
                link.external_attr = 0o120777 << 16
                archive.writestr(link, '/etc/passwd')

            with zipfile.ZipFile(archive_path, 'r') as archive:
                with self.assertRaises(ValueError):
                    panel_app.safe_extract_zip(archive, dest)

    def test_safe_extract_zip_blocks_too_many_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = os.path.join(tmp, 'too-many-entries.zip')
            dest = os.path.join(tmp, 'out')
            os.makedirs(dest, exist_ok=True)

            with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
                for index in range(3):
                    archive.writestr(f'f{index}.txt', 'x')

            with mock.patch.object(panel_app, 'ARCHIVE_MAX_ENTRIES', 2):
                with zipfile.ZipFile(archive_path, 'r') as archive:
                    with self.assertRaises(ValueError):
                        panel_app.safe_extract_zip(archive, dest)

    def test_safe_extract_zip_blocks_expanded_size_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = os.path.join(tmp, 'expanded-size-limit.zip')
            dest = os.path.join(tmp, 'out')
            os.makedirs(dest, exist_ok=True)

            with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('big.txt', 'A' * 4096)

            with mock.patch.object(panel_app, 'ARCHIVE_MAX_UNCOMPRESSED_BYTES', 32):
                with zipfile.ZipFile(archive_path, 'r') as archive:
                    with self.assertRaises(ValueError):
                        panel_app.safe_extract_zip(archive, dest)

    def test_safe_extract_zip_blocks_high_compression_ratio(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = os.path.join(tmp, 'ratio-limit.zip')
            dest = os.path.join(tmp, 'out')
            os.makedirs(dest, exist_ok=True)

            with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('ratio.txt', 'A' * 4096)

            with mock.patch.object(panel_app, 'ARCHIVE_MAX_COMPRESSION_RATIO', 2):
                with zipfile.ZipFile(archive_path, 'r') as archive:
                    with self.assertRaises(ValueError):
                        panel_app.safe_extract_zip(archive, dest)

    def test_health_endpoint_public_contract(self):
        client = self.app.test_client()
        response = client.get('/api/health')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'status': 'ok'})

    def test_notify_webhook_is_disabled_without_secret_by_default(self):
        client = self.app.test_client()
        response = client.post('/api/webhook/notify', json={'message': 'ping'})
        self.assertEqual(response.status_code, 503)

    def test_notify_webhook_requires_secret_and_valid_schema(self):
        client = self.app.test_client()
        with mock.patch.object(panel_app, 'NOTIFY_WEBHOOK_SECRET', 'notify-secret'):
            self.assertEqual(client.post('/api/webhook/notify', json={'message': 'ping'}).status_code, 403)
            response = client.post('/api/webhook/notify', json={'message': 'ping'}, headers={'X-Webhook-Secret': 'notify-secret'})
            self.assertEqual(response.status_code, 200)
            invalid = client.post('/api/webhook/notify', json={'message': {'bad': True}}, headers={'X-Webhook-Secret': 'notify-secret'})
            self.assertEqual(invalid.status_code, 400)

    def test_header_has_single_compact_language_switch(self):
        client = self.app.test_client()
        page = client.get('/login')
        html = page.get_data(as_text=True)
        self.assertEqual(html.count('class="lang-switcher compact-lang-switcher"'), 1)
        self.assertNotIn('Українська / English', html)

    def test_runtime_api_uses_safe_central_registry(self):
        response = self.app.test_client().get('/api/runtimes')
        self.assertEqual(response.status_code, 200)
        runtimes = response.get_json()['runtimes']
        self.assertEqual([item['id'] for item in runtimes], ['static', 'php', 'wordpress', 'node', 'python', 'docker'])
        self.assertTrue(all(item['available'] for item in runtimes))
        self.assertTrue(all('template' not in item and 'detail' not in item for item in runtimes))

    def test_runtime_detection_recommends_but_never_selects(self):
        owner = self._create_user('detect-owner')
        client = self.app.test_client()
        self._auth_session(client, owner.id)
        headers = {'X-CSRF-Token': 'csrf-token'}
        vite = client.post('/api/runtimes/detect', json={
            'files': ['package.json', 'vite.config.js', 'src/main.js'],
            'packageJson': {'scripts': {'build': 'vite build'}, 'devDependencies': {'vite': '^7'}},
        }, headers=headers).get_json()['detection']
        self.assertEqual(vite['recommended'], 'static')
        self.assertTrue(vite['requires_confirmation'])
        docker = client.post('/api/runtimes/detect', json={
            'files': ['Dockerfile', 'package.json'], 'packageJson': {'dependencies': {'express': '^5'}},
        }, headers=headers).get_json()['detection']
        self.assertEqual(docker['recommended'], 'docker')

    def test_create_site_uses_accessible_runtime_cards(self):
        owner = self._create_user('cards-owner')
        client = self.app.test_client()
        self._auth_session(client, owner.id)
        html = client.get('/sites/create').get_data(as_text=True)
        self.assertIn('class="runtime-card-grid"', html)
        self.assertEqual(html.count('<input type="radio" name="site_type"'), 6)
        self.assertIn('value="wordpress"', html)
        self.assertIn('id="wordpress-settings-block"', html)
        self.assertIn('name="spa_enabled"', html)

    def test_sites_index_has_real_filters_and_deployment_state(self):
        owner = self._create_user('sites-ui-owner')
        site = self._create_site(owner, name='sites-ui')
        panel_app.db.session.add(panel_app.DeploymentEvent(site_name=site.name, status='success', detail='verified'))
        panel_app.db.session.commit()
        client = self.app.test_client()
        self._auth_session(client, owner.id)
        with mock.patch.object(panel_app, 'actual_site_runtime_statuses', return_value={site.id: 'running'}):
            html = client.get('/sites').get_data(as_text=True)
        self.assertIn('data-list-filter="status"', html)
        self.assertIn('data-list-filter="runtime"', html)
        self.assertIn('data-status="running"', html)
        self.assertIn('data-runtime="static"', html)
        self.assertIn('Success', html)

    def test_site_workspace_uses_semantic_sections_and_shared_confirm(self):
        owner = self._create_user('workspace-ui-owner')
        site = self._create_site(owner, name='workspace-ui')
        client = self.app.test_client()
        self._auth_session(client, owner.id)
        domain_state = {'dns': 'verified', 'ssl': 'secure', 'detail': '', 'valid_until': None}
        with mock.patch.object(panel_app, 'probe_domain_status', return_value=domain_state), \
             mock.patch.object(panel_app, 'actual_site_runtime_status', return_value='running'), \
             mock.patch.object(panel_app, 'application_runtime_metrics', return_value={'containers': [], 'limits': {}}):
            html = client.get(f'/site/{site.folder_name}').get_data(as_text=True)
        self.assertIn('id="domain-ssl"', html)
        self.assertIn('id="backups"', html)
        self.assertEqual(html.count('id="files"'), 1)
        self.assertNotIn('onsubmit="return confirm(', html)


if __name__ == '__main__':
    unittest.main()
