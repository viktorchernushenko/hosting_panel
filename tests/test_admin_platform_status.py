import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault('HOSTING_PANEL_SECRET', 'test-secret-key-1234567890abcdef')
os.environ.setdefault('HOSTING_PANEL_DATABASE_URI', f"sqlite:////tmp/hosting-panel-status-tests-{os.getpid()}.db")

import app as panel_app


class AdminPlatformStatusTests(unittest.TestCase):
    def setUp(self):
        panel_app.app.config['TESTING'] = True
        panel_app.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        self.context = panel_app.app.app_context()
        self.context.push()
        panel_app.db.session.remove()
        panel_app.db.engine.dispose()
        panel_app.db.create_all()
        self.request_context = panel_app.app.test_request_context('/')
        self.request_context.push()

    def tearDown(self):
        panel_app.db.session.remove()
        panel_app.db.drop_all()
        self.request_context.pop()
        self.context.pop()

    def _build(self, remote='not_configured', configured=0, sqlite_ok=True, tls_ok=True, sftp_unknown=4):
        providers = [
            {'provider': 'telegram', 'configured': configured >= 1},
            {'provider': 'smtp', 'configured': configured >= 2},
        ]
        sftp = [{'classification': 'UNKNOWN'} for _ in range(sftp_unknown)]
        with (
            mock.patch.object(panel_app, 'platform_backup_status', return_value={'remote_status': remote}),
            mock.patch.object(panel_app, 'notification_statuses', return_value=providers),
            mock.patch.object(panel_app, 'sqlite_application_health', return_value={
                'status': 'healthy' if sqlite_ok else 'failed', 'integrity': 'ok' if sqlite_ok else 'failed',
            }),
            mock.patch.object(panel_app, 'edge_tls_certificate_status', return_value={
                'status': 'valid' if tls_ok else 'failed', 'days_remaining': 80 if tls_ok else None,
            }),
            mock.patch.object(panel_app, 'system_sftp_inventory', return_value=sftp),
            mock.patch.object(panel_app.SftpAccount.query, 'all', return_value=[]),
        ):
            return panel_app.admin_platform_status()

    def test_only_actionable_items_appear_in_attention(self):
        status = self._build()
        self.assertEqual(
            [item['id'] for item in status['attention_items']],
            ['offserver-backup', 'notification-providers', 'sftp-classification'],
        )
        self.assertEqual(
            [item['id'] for item in status['platform_status']],
            ['orphan-site-directories', 'application-database', 'edge-tls'],
        )
        required_fields = {'id', 'category', 'severity', 'status', 'title', 'message', 'action', 'action_url', 'requires_action'}
        self.assertTrue(all(required_fields <= set(item) for item in status['items']))

    def test_healthy_configuration_clears_attention(self):
        status = self._build(remote='verified', configured=2, sftp_unknown=0)
        self.assertEqual(status['attention_items'], [])
        self.assertEqual(len(status['platform_status']), 6)

    def test_failed_health_checks_are_actionable(self):
        status = self._build(remote='verified', configured=2, sqlite_ok=False, tls_ok=False, sftp_unknown=0)
        self.assertEqual(
            [item['id'] for item in status['attention_items']],
            ['application-database', 'edge-tls'],
        )


if __name__ == '__main__':
    unittest.main()
