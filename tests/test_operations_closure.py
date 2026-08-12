import json
import os
import tempfile
import unittest
from unittest import mock

from notification_service import DeliveryResult, NotificationService


class FakeProvider:
    name = 'fake'
    configured = True

    def __init__(self):
        self.calls = []

    def send(self, subject, message):
        self.calls.append((subject, message))
        return DeliveryResult(self.name, True, True, 'delivered')


class OperationsClosureTests(unittest.TestCase):
    def test_missing_notification_configuration_is_honest(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(os.environ, {}, clear=True):
            service = NotificationService.from_env(os.path.join(temporary, 'state.json'))
            self.assertEqual(service.statuses(), [
                {'provider': 'telegram', 'configured': False},
                {'provider': 'smtp', 'configured': False},
            ])
            result = service.send('test', force=True)
            self.assertFalse(result['delivered'])
            self.assertTrue(all(item['detail'] == 'not configured' for item in result['results']))

    def test_alert_deduplication_and_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            provider = FakeProvider()
            service = NotificationService([provider], os.path.join(temporary, 'state.json'), cooldown_seconds=3600)
            self.assertTrue(service.send('problem', alert_key='disk')['delivered'])
            self.assertTrue(service.send('problem again', alert_key='disk')['suppressed'])
            self.assertTrue(service.send('recovered', alert_key='disk', resolved=True)['delivered'])
            self.assertTrue(service.send('recovered again', alert_key='disk', resolved=True)['suppressed'])
            self.assertEqual(len(provider.calls), 2)
            with open(os.path.join(temporary, 'state.json')) as handle:
                state = json.load(handle)
            self.assertTrue(state['disk']['resolved'])


if __name__ == '__main__':
    unittest.main()
