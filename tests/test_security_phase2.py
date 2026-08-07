import io
import os
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault('HOSTING_PANEL_SECRET', 'test-secret-key-1234567890abcdef')

import app as panel_app


class SecurityPhase2Tests(unittest.TestCase):
    def setUp(self):
        self.app = panel_app.app
        self.app.config['TESTING'] = True
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        self.app_context = self.app.app_context()
        self.app_context.push()
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
        self.assertEqual(denied.status_code, 400)
        self.assertIn('CSRF', denied.get_data(as_text=True))

        # CSRF header present -> route executes and returns JSON payload
        allowed = client.post('/api/console', json={'command': 'invalid'}, headers={'X-CSRF-Token': 'csrf-token'})
        self.assertEqual(allowed.status_code, 400)
        payload = allowed.get_json()
        self.assertIsInstance(payload, dict)
        self.assertIn('success', payload)

    def test_unassigned_developer_cannot_access_foreign_site_status(self):
        owner = self._create_user('owner1', role='user', is_admin=False)
        developer = self._create_user('dev1', role='developer', is_admin=False)
        site = self._create_site(owner, name='private')

        client = self.app.test_client()
        self._auth_session(client, developer.id)
        response = client.get(f'/api/sites/{site.id}/status')
        self.assertEqual(response.status_code, 403)

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

    def test_header_has_single_compact_language_switch(self):
        client = self.app.test_client()
        page = client.get('/login')
        html = page.get_data(as_text=True)
        self.assertEqual(html.count('class="lang-switcher compact-lang-switcher"'), 1)
        self.assertNotIn('Українська / English', html)


if __name__ == '__main__':
    unittest.main()
