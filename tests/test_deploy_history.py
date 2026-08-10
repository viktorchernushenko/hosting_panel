import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault('HOSTING_PANEL_SECRET', 'test-secret-key-1234567890abcdef')
os.environ.setdefault('HOSTING_PANEL_DATABASE_URI', f"sqlite:////tmp/hosting-panel-tests-{os.getpid()}.db")

import app as panel_app


class DeployHistoryTests(unittest.TestCase):
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

    def test_record_deployment_event_persists(self):
        event = panel_app.record_deployment_event(site_name='demo', deploy_mode='git', status='success', detail='deployed', repo_url='https://example.com/repo.git')
        self.assertIsNotNone(event.id)
        self.assertEqual(panel_app.DeploymentEvent.query.count(), 1)
        saved = panel_app.DeploymentEvent.query.first()
        self.assertEqual(saved.site_name, 'demo')
        self.assertEqual(saved.status, 'success')

    def test_append_deploy_log_writes_file(self):
        path = panel_app.append_deploy_log('demo', 'deploy started')
        self.assertTrue(os.path.exists(path))
        with open(path, 'r', encoding='utf-8') as handle:
            content = handle.read()
        self.assertIn('deploy started', content)

    def test_github_webhook_route_requires_secret_by_default(self):
        client = self.app.test_client()
        response = client.post('/api/github/webhook', json={'repository': {'name': 'demo-repo'}, 'site_name': 'demo', 'ref': 'refs/heads/main'})
        self.assertEqual(response.status_code, 503)

    def test_github_webhook_route_allows_unsigned_when_compatibility_enabled(self):
        client = self.app.test_client()
        old_value = panel_app.ALLOW_UNSIGNED_GITHUB_WEBHOOK
        panel_app.ALLOW_UNSIGNED_GITHUB_WEBHOOK = True
        try:
            response = client.post('/api/github/webhook', json={'repository': {'name': 'demo-repo'}, 'site_name': 'demo', 'ref': 'refs/heads/main'})
            self.assertEqual(response.status_code, 200)
        finally:
            panel_app.ALLOW_UNSIGNED_GITHUB_WEBHOOK = old_value

    def test_ensure_default_admin_user_creates_admin(self):
        panel_app.ensure_default_admin_user()
        user = panel_app.User.query.filter_by(username='developer').first()
        self.assertIsNotNone(user)
        self.assertTrue(user.is_admin)


if __name__ == '__main__':
    unittest.main()
