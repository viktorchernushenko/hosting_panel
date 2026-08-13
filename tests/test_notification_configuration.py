import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault('HOSTING_PANEL_SECRET', 'test-secret-key-1234567890abcdef')
os.environ.setdefault('HOSTING_PANEL_DATABASE_URI', f"sqlite:////tmp/hosting-panel-notification-tests-{os.getpid()}.db")

import app as panel_app
from notification_config import NotificationConfigStore


class NotificationConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.app = panel_app.app
        self.app.config['TESTING'] = True
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        self.context = self.app.app_context()
        self.context.push()
        panel_app.db.session.remove()
        panel_app.db.engine.dispose()
        panel_app.db.drop_all()
        panel_app.db.create_all()
        self.temporary = tempfile.TemporaryDirectory()
        self.store = NotificationConfigStore(
            os.path.join(self.temporary.name, 'notification_config.enc'),
            'test-secret-key-1234567890abcdef',
        )
        self.store_patch = mock.patch.object(panel_app, 'notification_config_store', return_value=self.store)
        self.store_patch.start()
        self.admin = self._user('admin', 'admin', True)
        self.regular = self._user('regular', 'user', False)

    def tearDown(self):
        self.store_patch.stop()
        panel_app.db.session.remove()
        panel_app.db.drop_all()
        self.context.pop()
        self.temporary.cleanup()

    def _user(self, username, role, is_admin):
        user = panel_app.User(username=username, first_name='Test', last_name='User', phone='0',
                              email=f'{username}@example.test', password='hash', role=role, is_admin=is_admin)
        panel_app.db.session.add(user)
        panel_app.db.session.commit()
        return user

    def _auth(self, client, user, csrf='csrf-token'):
        with client.session_transaction() as session:
            session['user_id'] = user.id
            session['username'] = user.username
            session['role'] = user.role
            session['_csrf_token'] = csrf

    def test_store_encrypts_secrets_and_uses_restrictive_permissions(self):
        token = '123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef'
        self.store.update_provider('telegram', {'enabled': True, 'bot_token': token, 'chat_id': '-1001234567890'})
        raw = self.store.path.read_bytes()
        self.assertNotIn(token.encode(), raw)
        self.assertEqual(stat.S_IMODE(self.store.path.stat().st_mode), 0o600)
        self.assertEqual(self.store.load()['providers']['telegram']['bot_token'], token)

    def test_admin_can_save_without_secret_leak_and_blank_secret_is_preserved(self):
        client = self.app.test_client()
        self._auth(client, self.admin)
        token = '123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef'
        response = client.post('/api/admin/notifications/telegram/configure', data={
            '_csrf_token': 'csrf-token', 'bot_token': token, 'chat_id': '-1001234567890', 'enabled': '1',
        })
        self.assertEqual(response.status_code, 302)
        api = client.get('/api/admin/notifications/settings')
        self.assertEqual(api.status_code, 200)
        self.assertNotIn(token, api.get_data(as_text=True))
        html = client.get('/developer/notifications').get_data(as_text=True)
        self.assertNotIn(token, html)
        self.assertNotIn(token, '\n'.join(row.detail for row in panel_app.AuditLog.query.all()))
        with self.app.test_request_context('/'):
            self.store.update_provider('telegram', {'enabled': True, 'bot_token': '', 'chat_id': '-1009999999999'})
        self.assertEqual(self.store.load()['providers']['telegram']['bot_token'], token)

    def test_non_admin_and_csrf_are_rejected(self):
        client = self.app.test_client()
        self._auth(client, self.regular)
        self.assertEqual(client.get('/api/admin/notifications/settings').status_code, 403)
        self.assertEqual(client.post('/api/admin/notifications/telegram/configure', data={'_csrf_token': 'csrf-token'}).status_code, 403)
        self._auth(client, self.admin)
        self.assertEqual(client.post('/api/admin/notifications/telegram/configure', data={}).status_code, 403)

    def test_real_test_state_changes_only_after_provider_success(self):
        client = self.app.test_client()
        self._auth(client, self.admin)
        token = '123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef'
        client.post('/api/admin/notifications/telegram/configure', data={
            '_csrf_token': 'csrf-token', 'bot_token': token, 'chat_id': '-1001234567890', 'enabled': '1',
        })
        before = {row['provider']: row for row in panel_app.notification_statuses()}
        self.assertEqual(before['telegram']['status'], 'ERROR')
        fake_service = mock.Mock()
        fake_service.send.return_value = {'delivered': True, 'results': []}
        test_status = {'provider': 'telegram', 'credentials_saved': True}
        with (mock.patch.object(panel_app, 'notification_statuses', return_value=[test_status]),
              mock.patch.object(panel_app.NotificationService, 'from_env', return_value=fake_service)):
            response = client.post('/api/admin/notifications/telegram/test', data={'_csrf_token': 'csrf-token'})
        self.assertEqual(response.status_code, 302)
        after = {row['provider']: row for row in panel_app.notification_statuses()}
        self.assertEqual(after['telegram']['status'], 'CONFIGURED')

    def test_test_endpoint_is_rate_limited(self):
        client = self.app.test_client()
        self._auth(client, self.admin)
        with mock.patch.object(panel_app, 'notification_test_rate_allowed', return_value=False):
            response = client.post('/api/admin/notifications/telegram/test',
                                   json={}, headers={'X-CSRF-Token': 'csrf-token'})
        self.assertEqual(response.status_code, 429)


if __name__ == '__main__':
    unittest.main()
