import os
import sys
import json
import io
import zipfile
import unittest
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault('HOSTING_PANEL_SECRET', 'test-secret-key-1234567890abcdef')
os.environ.setdefault('HOSTING_PANEL_DATABASE_URI', f"sqlite:////tmp/hosting-panel-tests-{os.getpid()}.db")

import app as panel_app


class ServiceCatalogWizardTests(unittest.TestCase):
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

        self.admin = self._create_user('admin', role='admin', is_admin=True)
        self.user = self._create_user('alice', role='user', is_admin=False)
        self.viewer = self._create_user('viewer', role='viewer', is_admin=False)
        self.developer = self._create_user('dev', role='developer', is_admin=False)

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

    def _auth_session(self, client, user_id, role='user', csrf_token='csrf-token'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id
            sess['username'] = 'test'
            sess['role'] = role
            sess['_csrf_token'] = csrf_token

    def _create_site(self, owner, name='demo'):
        site = panel_app.Site(
            name=name,
            folder_name=f'{owner.username}_{name}',
            user_id=owner.id,
        )
        panel_app.db.session.add(site)
        panel_app.db.session.commit()
        return site

    def _assign_user_permissions(self, site, assigned_user, permissions):
        access = panel_app.ensure_application_access(site)
        access.assigned_users_json = json.dumps([assigned_user.id])
        access.permissions_user_json = json.dumps(permissions)
        panel_app.db.session.commit()
        return access

    def _assign_developer_permissions(self, site, developer_user, permissions):
        access = panel_app.ensure_application_access(site)
        access.assigned_developers_json = json.dumps([developer_user.id])
        access.permissions_developer_json = json.dumps(permissions)
        panel_app.db.session.commit()
        return access

    def test_service_catalog_requires_auth(self):
        client = self.app.test_client()
        resp = client.get('/service-catalog')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp.headers.get('Location', ''))

    def test_service_catalog_for_user(self):
        client = self.app.test_client()
        self._auth_session(client, self.user.id, role='user')
        resp = client.get('/service-catalog')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('static-site', html)
        self.assertIn('create-sftp', html)

    def test_git_repository_validation_blocks_local_and_embedded_credentials(self):
        self.assertEqual(panel_app.validate_git_repository_url('file:///etc/passwd', 'generic')[1], 'GIT_URL_INVALID')
        self.assertEqual(panel_app.validate_git_repository_url('https://token@github.com/a/b.git', 'github')[1], 'GIT_URL_INVALID')
        self.assertEqual(panel_app.validate_git_repository_url('https://gitlab.com/a/b.git', 'github')[1], 'GIT_PROVIDER_MISMATCH')

    @mock.patch.object(panel_app.subprocess, 'run')
    def test_git_connection_uses_argument_array_and_detects_branch(self, mocked_run):
        mocked_run.return_value = mock.Mock(returncode=0, stdout='a' * 40 + '\trefs/heads/main\n', stderr='')
        result = panel_app.test_git_connection('https://github.com/example/repo.git', 'main', 'github', 'public')
        self.assertTrue(result['success'])
        args, kwargs = mocked_run.call_args
        self.assertEqual(args[0][:4], ['git', 'ls-remote', '--exit-code', '--heads'])
        self.assertFalse(kwargs.get('shell', False))

    @mock.patch.object(panel_app, 'test_git_connection')
    def test_git_wizard_saves_write_only_token(self, mocked_test):
        mocked_test.return_value = {'success': True, 'code': 'GIT_CONNECTED', 'commit': 'a' * 40}
        site = self._create_site(self.user, name='gitdemo')
        client = self.app.test_client()
        self._auth_session(client, self.user.id, role='user')
        with mock.patch.object(panel_app, 'write_integration_secret') as write_secret:
            write_secret.side_effect = lambda integration, payload: setattr(integration, 'secret_ref', '/safe/secret') or '/safe/secret'
            response = client.post('/service-catalog/git', json={
                'action': 'save', 'application_id': site.id, 'provider': 'github',
                'repository_url': 'https://github.com/example/repo.git', 'branch': 'main',
                'auth_type': 'pat', 'token': 'never-return-this-token',
            }, headers={'X-CSRF-Token': 'csrf-token'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('never-return-this-token', response.get_data(as_text=True))
        integration = panel_app.Integration.query.filter_by(application_id=site.id).first()
        self.assertIsNotNone(integration)
        self.assertNotIn('never-return-this-token', integration.config_json)

    def test_create_site_wizard_requires_permission(self):
        client = self.app.test_client()
        self._auth_session(client, self.viewer.id, role='viewer')
        resp = client.get('/sites/create', follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/dashboard', resp.headers.get('Location', ''))

    def test_create_site_wizard_creates_site_and_stack(self):
        client = self.app.test_client()
        self._auth_session(client, self.admin.id, role='admin')
        resp = client.post(
            '/sites/create',
            data={
                '_csrf_token': 'csrf-token',
                'site_name': 'wizarddemo',
                'site_type': 'node',
                'source_mode': 'git',
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)

        site = panel_app.Site.query.filter_by(name='wizarddemo').first()
        self.assertIsNotNone(site)
        self.assertEqual(site.php_version, 'node')

        stack_root = os.path.join(self.app.instance_path, 'app_stacks', site.folder_name)
        self.assertTrue(os.path.isdir(stack_root))
        self.assertTrue(os.path.exists(os.path.join(stack_root, 'panel-metadata.json')))

    def test_wordpress_one_click_uses_php_application_type_and_does_not_log_password(self):
        client = self.app.test_client()
        self._auth_session(client, self.admin.id, role='admin')
        secret = 'Wp-Test-Password-That-Must-Not-Leak-123!'
        metadata = {'runtime': 'wordpress', 'version': '6-php8.3-fpm-alpine', 'port': 23456}
        def prepare_stack(stack_root, _source_root, _port):
            os.makedirs(stack_root, exist_ok=True)
            return metadata
        with (
            mock.patch.object(panel_app, 'prepare_wordpress_runtime', side_effect=prepare_stack),
            mock.patch.object(panel_app, 'provision_mysql_database'),
            mock.patch.object(panel_app, 'write_application_secret', return_value='/safe/database-secret'),
            mock.patch.object(panel_app, 'sync_runtime_environment'),
            mock.patch.object(panel_app, 'install_wordpress_one_click') as install,
        ):
            response = client.post('/sites/create', data={
                '_csrf_token': 'csrf-token', 'site_name': 'managedwp', 'site_type': 'wordpress',
                'source_mode': 'upload', 'wordpress_mode': 'one_click', 'wordpress_title': 'Managed WP',
                'wordpress_admin': 'wp_owner', 'wordpress_email': 'owner@example.test',
                'wordpress_password': secret, 'wordpress_language': 'uk',
            }, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        site = panel_app.Site.query.filter_by(name='managedwp').first()
        self.assertEqual(site.runtime_type, 'php')
        self.assertEqual(site.runtime_version, '8.3')
        self.assertEqual(site.application_type, 'wordpress')
        self.assertEqual(len(site.database_resources), 1)
        self.assertTrue(install.called)
        self.assertNotIn(secret, '\n'.join(row.detail for row in panel_app.AuditLog.query.all()))

    def test_wordpress_one_click_rejects_weak_admin_password(self):
        client = self.app.test_client()
        self._auth_session(client, self.admin.id, role='admin')
        response = client.post('/sites/create', data={
            '_csrf_token': 'csrf-token', 'site_name': 'weakwp', 'site_type': 'wordpress',
            'source_mode': 'upload', 'wordpress_mode': 'one_click', 'wordpress_admin': 'owner',
            'wordpress_email': 'owner@example.test', 'wordpress_password': 'short', 'wordpress_language': 'uk',
        }, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(panel_app.Site.query.filter_by(name='weakwp').first())

    @mock.patch.object(panel_app, 'healthcheck', return_value={'ok': True})
    @mock.patch.object(panel_app, 'compose_action', return_value=(0, 'started'))
    @mock.patch.object(panel_app, 'sync_runtime_environment')
    @mock.patch.object(panel_app, 'prepare_runtime')
    def test_start_repairs_missing_node_runtime_configuration(
        self, mocked_prepare, _mocked_sync, _mocked_compose, _mocked_health,
    ):
        site = self._create_site(self.admin, name='repairnode')
        site.runtime_type = 'node'
        site.runtime_version = '22'
        site.runtime_status = 'error'
        site.deployment_status = 'failed'
        panel_app.db.session.commit()
        access = panel_app.ensure_application_access(site)
        os.makedirs(access.file_root, exist_ok=True)
        with open(os.path.join(access.file_root, 'package.json'), 'w', encoding='utf-8') as handle:
            json.dump({
                'scripts': {'build': 'vite build', 'start': 'node server/node-runtime.js'},
            }, handle)
        with open(os.path.join(access.file_root, 'package-lock.json'), 'w', encoding='utf-8') as handle:
            handle.write('{}')

        def prepare(stack_root, _site_path, _runtime_type, _version, port, **_kwargs):
            os.makedirs(stack_root, exist_ok=True)
            with open(os.path.join(stack_root, 'runtime.json'), 'w', encoding='utf-8') as handle:
                json.dump({'project': 'repairnode', 'port': port}, handle)
            return {'project': 'repairnode', 'port': port}

        mocked_prepare.side_effect = prepare
        client = self.app.test_client()
        self._auth_session(client, self.admin.id, role='admin')
        response = client.post(
            f'/site/{site.id}/runtime/start',
            data={'_csrf_token': 'csrf-token'},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        panel_app.db.session.refresh(site)
        panel_app.db.session.refresh(access)
        self.assertEqual(site.runtime_status, 'running')
        self.assertEqual(site.deployment_status, 'success')
        self.assertTrue(os.path.isfile(os.path.join(access.deployment_root, 'runtime.json')))
        self.assertEqual(site.install_command, 'npm ci')
        self.assertEqual(site.build_command, 'npm run build')
        self.assertEqual(site.start_command, 'npm start')
        mocked_prepare.assert_called_once()
        _mocked_health.assert_called_once_with(mock.ANY, timeout=90)

    def test_prepare_runtime_tolerates_sftp_owned_source_directory(self):
        with tempfile.TemporaryDirectory() as temp_root:
            stack_root = os.path.join(temp_root, 'stack')
            source_root = os.path.join(temp_root, 'source')
            os.makedirs(source_root)
            original_chmod = Path.chmod

            def guarded_chmod(path, mode):
                if path.resolve() == Path(source_root).resolve():
                    raise PermissionError(1, 'Operation not permitted')
                return original_chmod(path, mode)

            with mock.patch('runtime_engine.Path.chmod', autospec=True, side_effect=guarded_chmod):
                metadata = panel_app.prepare_runtime(
                    stack_root,
                    source_root,
                    'node',
                    '22',
                    18080,
                    start_command='npm start',
                )
            self.assertEqual(metadata['runtime'], 'node')
            self.assertTrue(os.path.isfile(os.path.join(stack_root, 'runtime.json')))

    def test_registry_reports_actual_runtime_type(self):
        site = self._create_site(self.admin, name='registrynode')
        site.runtime_type = 'node'
        panel_app.db.session.commit()
        with mock.patch.object(panel_app.urllib.request, 'urlopen') as urlopen:
            urlopen.return_value.__enter__.return_value.status = 200
            registry = panel_app.build_application_registry()
        row = next(item for item in registry['applications'] if item['id'] == site.id)
        self.assertEqual(row['type'], 'Node.js')
        self.assertEqual(row['stack'], 'node')

    def test_backup_root_is_scoped_by_id_and_folder(self):
        first = mock.Mock(id=1, folder_name='alice-old', name='old')
        replacement = mock.Mock(id=1, folder_name='alice-new', name='new')
        self.assertNotEqual(
            panel_app.scoped_site_backup_root(first),
            panel_app.scoped_site_backup_root(replacement),
        )

    def test_create_php_site_wizard_runtime_and_bootstrap(self):
        client = self.app.test_client()
        self._auth_session(client, self.admin.id, role='admin')
        resp = client.post(
            '/sites/create',
            data={
                '_csrf_token': 'csrf-token',
                'site_name': 'phpwizard',
                'site_type': 'php',
                'source_mode': 'upload',
                'php_runtime': '8.2',
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)

        site = panel_app.Site.query.filter_by(name='phpwizard').first()
        self.assertIsNotNone(site)
        self.assertEqual(site.php_version, 'php')

        access = panel_app.ensure_application_access(site)
        file_root = panel_app.application_root(access, bucket='file')
        self.assertTrue(os.path.isfile(os.path.join(file_root, 'index.php')))

        stack_root = panel_app.application_root(access, bucket='deployment')
        metadata_path = os.path.join(stack_root, 'panel-metadata.json')
        self.assertTrue(os.path.isfile(metadata_path))
        with open(metadata_path, 'r', encoding='utf-8') as handle:
            payload = json.load(handle)
        self.assertEqual(payload.get('runtime', {}).get('language'), 'php')
        self.assertEqual(payload.get('runtime', {}).get('version'), '8.2')

    def test_domain_update_requires_domain_manage_permission(self):
        site = self._create_site(self.admin, name='domdemo')
        self._assign_user_permissions(site, self.user, ['site.view'])

        client = self.app.test_client()
        self._auth_session(client, self.user.id, role='user')
        resp = client.post(
            f'/site/{site.id}/domain',
            data={'_csrf_token': 'csrf-token', 'custom_domain': 'example.test'},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 403)

    def test_backup_download_requires_backup_download_permission(self):
        site = self._create_site(self.admin, name='bakdemo')
        self._assign_user_permissions(site, self.user, ['backup.view'])

        client = self.app.test_client()
        self._auth_session(client, self.user.id, role='user')
        resp = client.get(f'/site/{site.id}/backup/20260807-101010.zip/download')
        self.assertEqual(resp.status_code, 403)

    def test_backup_restore_queue_requires_backup_restore_permission(self):
        site = self._create_site(self.admin, name='restoredemo')
        self._assign_user_permissions(site, self.user, ['deployment.request'])

        client = self.app.test_client()
        self._auth_session(client, self.user.id, role='user')
        resp = client.post(
            f'/site/{site.id}/backup/20260807-101010.zip/restore/queue',
            data={'_csrf_token': 'csrf-token'},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 403)

    def test_backup_delete_requires_backup_restore_permission(self):
        site = self._create_site(self.admin, name='deldeny')
        self._assign_user_permissions(site, self.user, ['backup.download'])

        access = panel_app.ensure_application_access(site)
        backup_name = '20260807-180000.zip'
        backup_dir = panel_app.backup_directory(site, access=access)
        os.makedirs(backup_dir, exist_ok=True)
        with open(os.path.join(backup_dir, backup_name), 'wb') as handle:
            handle.write(b'backup')

        client = self.app.test_client()
        self._auth_session(client, self.user.id, role='user')
        resp = client.post(
            f'/site/{site.id}/backup/{backup_name}/delete',
            data={'_csrf_token': 'csrf-token'},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 403)

    def test_backup_delete_removes_archive(self):
        site = self._create_site(self.admin, name='delsuccess')
        access = panel_app.ensure_application_access(site)
        backup_name = '20260807-180500.zip'
        backup_dir = panel_app.backup_directory(site, access=access)
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(backup_dir, backup_name)
        with open(backup_path, 'wb') as handle:
            handle.write(b'backup-data')

        client = self.app.test_client()
        self._auth_session(client, self.admin.id, role='admin')
        resp = client.post(
            f'/site/{site.id}/backup/{backup_name}/delete',
            data={'_csrf_token': 'csrf-token'},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(os.path.exists(backup_path))

    def test_site_status_requires_health_view_permission(self):
        site = self._create_site(self.admin, name='healthdemo')
        self._assign_user_permissions(site, self.user, ['site.view'])

        client = self.app.test_client()
        self._auth_session(client, self.user.id, role='user')
        resp = client.get(f'/api/sites/{site.id}/status')
        self.assertEqual(resp.status_code, 403)

    def test_php_health_requires_health_view_permission(self):
        site = self._create_site(self.admin, name='phphealthdenied')
        site.php_version = 'php'
        panel_app.db.session.commit()
        self._assign_user_permissions(site, self.user, ['site.view'])

        client = self.app.test_client()
        self._auth_session(client, self.user.id, role='user')
        resp = client.get(f'/api/sites/{site.id}/php-health')
        self.assertEqual(resp.status_code, 403)

    def test_php_health_endpoint_for_php_site(self):
        client = self.app.test_client()
        self._auth_session(client, self.admin.id, role='admin')
        create_resp = client.post(
            '/sites/create',
            data={
                '_csrf_token': 'csrf-token',
                'site_name': 'phphealthok',
                'site_type': 'php',
                'source_mode': 'upload',
                'php_runtime': '8.2',
            },
            follow_redirects=False,
        )
        self.assertEqual(create_resp.status_code, 302)

        site = panel_app.Site.query.filter_by(name='phphealthok').first()
        self.assertIsNotNone(site)

        with mock.patch.object(panel_app, 'run_command', return_value=(0, 'PHP 8.2.22 (cli)')):
            resp = client.get(f'/api/sites/{site.id}/php-health')

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get('ok'))
        self.assertTrue(data.get('bootstrap_exists'))
        self.assertEqual(data.get('runtime_language'), 'php')
        self.assertEqual(data.get('runtime_version'), '8.2')
        self.assertTrue(data.get('php_binary_available'))

    def test_api_php_runtime_check(self):
        client = self.app.test_client()
        self._auth_session(client, self.admin.id, role='admin')

        with mock.patch.object(panel_app, 'run_command', return_value=(0, 'PHP 8.2.21 (cli)')):
            resp = client.post(
                '/api/php/runtime-check',
                headers={'X-CSRF-Token': 'csrf-token'},
                json={'php_runtime': '8.2'},
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertTrue(payload.get('success'))
        self.assertTrue(payload.get('php_available'))
        self.assertEqual(payload.get('installed_version'), '8.2.21')
        self.assertTrue(payload.get('compatible'))

    def test_deploy_git_php_runtime_mismatch_blocked(self):
        site = self._create_site(self.admin, name='phpgit')
        site.php_version = 'php'
        panel_app.db.session.commit()

        access = panel_app.ensure_application_access(site)
        stack_root = panel_app.scaffold_application_stack(site.folder_name, 'php', source_mode='git', runtime_version='8.3')
        access.deployment_root = stack_root
        panel_app.db.session.commit()

        self._assign_developer_permissions(site, self.developer, ['deployment.execute', 'git.connect'])

        client = self.app.test_client()
        self._auth_session(client, self.developer.id, role='developer')

        with mock.patch.object(panel_app, 'run_command', return_value=(0, 'PHP 8.2.18 (cli)')):
            with mock.patch.object(panel_app, 'deploy_from_git') as mocked_deploy:
                resp = client.post(
                    '/developer/deploy',
                    data={
                        '_csrf_token': 'csrf-token',
                        'deploy_mode': 'git',
                        'target_name': 'phpgit',
                        'repo_url': 'https://example.test/repo.git',
                    },
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 302)
        mocked_deploy.assert_not_called()

    def test_sftp_access_requires_role_permission(self):
        client = self.app.test_client()
        self._auth_session(client, self.viewer.id, role='viewer')
        resp = client.get('/dashboard/sftp-access')
        self.assertEqual(resp.status_code, 403)

    def test_dashboard_stale_session_redirects_to_login(self):
        client = self.app.test_client()
        self._auth_session(client, 999999, role='user')
        resp = client.get('/dashboard', follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp.headers.get('Location', ''))

    def test_developer_logs_default_lines_no_type_error(self):
        site = self._create_site(self.admin, name='logdemo')
        self._assign_developer_permissions(site, self.developer, ['logs.view'])

        client = self.app.test_client()
        self._auth_session(client, self.developer.id, role='developer')
        resp = client.get('/developer/logs')
        self.assertEqual(resp.status_code, 200)

    def test_view_site_creates_default_index_if_missing(self):
        site = self._create_site(self.user, name='previewdemo')
        access = panel_app.ensure_application_access(site)
        root = panel_app.application_root(access, bucket='file')
        os.makedirs(root, exist_ok=True)
        index_path = os.path.join(root, 'index.html')
        if os.path.exists(index_path):
            os.remove(index_path)

        client = self.app.test_client()
        resp = client.get(f'/view-site/{site.folder_name}/')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(os.path.exists(index_path))

    def test_subdomain_root_creates_default_index_if_missing(self):
        site = self._create_site(self.user, name='subdemo')
        site_root = os.path.join(self.app.config['UPLOAD_FOLDER'], site.folder_name)
        os.makedirs(site_root, exist_ok=True)
        index_path = os.path.join(site_root, 'index.html')
        if os.path.exists(index_path):
            os.remove(index_path)

        client = self.app.test_client()
        resp = client.get('/', headers={'Host': 'subdemo.myh.guru'})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(os.path.exists(index_path))

    def test_subdomain_root_redirects_to_nested_entry_when_root_is_scaffold(self):
        site = self._create_site(self.user, name='nesteddemo')
        site_root = os.path.join(self.app.config['UPLOAD_FOLDER'], site.folder_name)
        os.makedirs(site_root, exist_ok=True)
        panel_app.scaffold_site_content(site_root, site.name)
        os.makedirs(os.path.join(site_root, '42'), exist_ok=True)
        with open(os.path.join(site_root, '42', 'index.html'), 'w', encoding='utf-8') as handle:
            handle.write('<h1>real nested site</h1>')

        client = self.app.test_client()
        resp = client.get('/', headers={'Host': 'nesteddemo.myh.guru'}, follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers.get('Location'), '/42/')

    def test_manage_site_directory_navigation_not_flat(self):
        site = self._create_site(self.user, name='treedemo')
        access = panel_app.ensure_application_access(site)
        root = panel_app.application_root(access, bucket='file')
        os.makedirs(os.path.join(root, '42', 'images'), exist_ok=True)
        with open(os.path.join(root, '42', 'images', 'background.png'), 'wb') as handle:
            handle.write(b'png')

        client = self.app.test_client()
        self._auth_session(client, self.user.id, role='user')

        root_resp = client.get(f'/site/{site.folder_name}')
        self.assertEqual(root_resp.status_code, 200)
        root_html = root_resp.get_data(as_text=True)
        self.assertIn('42', root_html)
        self.assertNotIn('42/images/background.png', root_html)

        nested_resp = client.get(f'/site/{site.folder_name}?dir=42/images')
        self.assertEqual(nested_resp.status_code, 200)
        nested_html = nested_resp.get_data(as_text=True)
        self.assertIn('42/images/background.png', nested_html)

    def test_zip_upload_extracts_into_selected_directory(self):
        site = self._create_site(self.user, name='zipdemo')
        access = panel_app.ensure_application_access(site)
        root = panel_app.application_root(access, bucket='file')
        os.makedirs(os.path.join(root, '42', 'images'), exist_ok=True)

        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('screenshots/shot.txt', 'ok')
        archive_buffer.seek(0)

        client = self.app.test_client()
        self._auth_session(client, self.user.id, role='user')
        resp = client.post(
            f'/site/{site.folder_name}?dir=42',
            data={
                '_csrf_token': 'csrf-token',
                'action': 'upload',
                'current_dir': '42',
                'target_dir': '42',
                'files': (archive_buffer, 'demo.zip'),
            },
            content_type='multipart/form-data',
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(os.path.isfile(os.path.join(root, '42', 'screenshots', 'shot.txt')))

    def test_zip_upload_clean_mode_flattens_single_top_folder_at_root(self):
        site = self._create_site(self.user, name='zipclean')
        access = panel_app.ensure_application_access(site)
        root = panel_app.application_root(access, bucket='file')
        os.makedirs(root, exist_ok=True)

        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('42/index.html', '<h1>nested</h1>')
            archive.writestr('42/images/logo.png', 'png-bytes')
        archive_buffer.seek(0)

        client = self.app.test_client()
        self._auth_session(client, self.user.id, role='user')
        resp = client.post(
            f'/site/{site.folder_name}',
            data={
                '_csrf_token': 'csrf-token',
                'action': 'upload',
                'current_dir': '',
                'target_dir': '',
                'files': (archive_buffer, 'nested.zip'),
            },
            content_type='multipart/form-data',
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(os.path.isfile(os.path.join(root, 'index.html')))
        self.assertTrue(os.path.isfile(os.path.join(root, 'images', 'logo.png')))
        self.assertFalse(os.path.isdir(os.path.join(root, '42')))

    def test_subdomain_root_does_not_create_scaffold_when_content_exists(self):
        site = self._create_site(self.user, name='noscaffold')
        site_root = os.path.join(self.app.config['UPLOAD_FOLDER'], site.folder_name)
        os.makedirs(os.path.join(site_root, 'assets'), exist_ok=True)
        with open(os.path.join(site_root, 'assets', 'app.js'), 'w', encoding='utf-8') as handle:
            handle.write('console.log("ok")')
        index_path = os.path.join(site_root, 'index.html')
        if os.path.exists(index_path):
            os.remove(index_path)

        client = self.app.test_client()
        resp = client.get('/', headers={'Host': 'noscaffold.myh.guru'}, follow_redirects=False)
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(os.path.exists(index_path))

    def test_deploy_webhook_config_requires_integration_manage(self):
        site = self._create_site(self.admin, name='hookdemo')
        self._assign_developer_permissions(site, self.developer, ['deployment.execute'])

        client = self.app.test_client()
        self._auth_session(client, self.developer.id, role='developer')
        resp = client.post(
            '/developer/deploy',
            data={
                '_csrf_token': 'csrf-token',
                'action': 'save_webhook_config',
                'site_name': 'hookdemo',
                'webhook_secret': 'secret123',
                'webhook_branch': 'main',
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 403)

    def test_deploy_git_requires_git_connect_permission(self):
        site = self._create_site(self.admin, name='gitdemo')
        self._assign_developer_permissions(site, self.developer, ['deployment.execute'])

        client = self.app.test_client()
        self._auth_session(client, self.developer.id, role='developer')

        with mock.patch.object(panel_app, 'deploy_from_git') as mocked_deploy:
            resp = client.post(
                '/developer/deploy',
                data={
                    '_csrf_token': 'csrf-token',
                    'deploy_mode': 'git',
                    'target_name': 'gitdemo',
                    'repo_url': 'https://example.test/repo.git',
                },
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 403)
        mocked_deploy.assert_not_called()

    def test_dashboard_shows_sftp_access_for_regular_user(self):
        client = self.app.test_client()
        self._auth_session(client, self.user.id, role='user')
        resp = client.get('/dashboard')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('/dashboard/sftp-access', html)

    def test_user_can_enable_and_disable_own_sftp(self):
        site = self._create_site(self.user, name='sftpself')
        panel_app.ensure_application_access(site)

        client = self.app.test_client()
        self._auth_session(client, self.user.id, role='user')

        with mock.patch.object(panel_app, 'queue_sftp_provision') as mocked_queue:
            enable_resp = client.post(
                '/dashboard/sftp-access/toggle',
                data={'_csrf_token': 'csrf-token', 'desired_state': 'enable'},
                follow_redirects=False,
            )
        self.assertEqual(enable_resp.status_code, 302)
        account = panel_app.SftpAccount.query.filter_by(assigned_user_id=self.user.id).first()
        self.assertIsNotNone(account)
        self.assertTrue(account.enabled)
        self.assertEqual(account.username, self.user.username)
        self.assertTrue(account.password_hash)
        self.assertNotEqual(account.password_hash, self.user.password)
        mocked_queue.assert_called()

        with mock.patch.object(panel_app, 'queue_sftp_provision') as mocked_queue_disable:
            disable_resp = client.post(
                '/dashboard/sftp-access/toggle',
                data={'_csrf_token': 'csrf-token', 'desired_state': 'disable'},
                follow_redirects=False,
            )
        self.assertEqual(disable_resp.status_code, 302)
        account = panel_app.SftpAccount.query.filter_by(assigned_user_id=self.user.id).first()
        self.assertFalse(account.enabled)
        mocked_queue_disable.assert_called()


if __name__ == '__main__':
    unittest.main()
