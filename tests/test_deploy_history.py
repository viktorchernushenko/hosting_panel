import hashlib
import hmac
import json
import os
import sys
import unittest
from unittest import mock

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

    def test_github_webhook_hmac_rejects_bad_and_accepts_valid_signature(self):
        client = self.app.test_client()
        payload = {'repository': {'name': 'demo-repo'}, 'site_name': 'default', 'ref': 'refs/heads/main'}
        body = json.dumps(payload, separators=(',', ':')).encode()
        with mock.patch.dict(panel_app.os.environ, {'GITHUB_WEBHOOK_SECRET': 'webhook-secret'}):
            bad = client.post('/api/github/webhook', data=body, content_type='application/json', headers={'X-Hub-Signature-256': 'sha256=bad'})
            self.assertEqual(bad.status_code, 401)
            signature = 'sha256=' + hmac.new(b'webhook-secret', body, hashlib.sha256).hexdigest()
            valid = client.post('/api/github/webhook', data=body, content_type='application/json', headers={'X-Hub-Signature-256': signature})
            self.assertEqual(valid.status_code, 200)

    def test_ensure_default_admin_user_creates_admin(self):
        panel_app.ensure_default_admin_user()
        user = panel_app.User.query.filter_by(username='developer').first()
        self.assertIsNotNone(user)
        self.assertTrue(user.is_admin)


if __name__ == '__main__':
    unittest.main()
