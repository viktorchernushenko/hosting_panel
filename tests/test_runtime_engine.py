import json
import os
import tempfile
import unittest
from unittest import mock

from runtime_engine import detect_file_names, healthcheck, prepare_custom_docker, prepare_runtime, prepare_wordpress_runtime, validate_custom_compose
from runtime_registry import runtime_catalog


class RuntimeEngineTests(unittest.TestCase):
    def test_static_spa_is_opt_in(self):
        with tempfile.TemporaryDirectory() as root:
            stack = os.path.join(root, 'stack')
            source = os.path.join(root, 'source')
            metadata = prepare_runtime(stack, source, 'static', spa_enabled=True)
            self.assertTrue(metadata['spa_enabled'])
            with open(os.path.join(stack, 'default.conf'), encoding='utf-8') as handle:
                config = handle.read()
            self.assertIn('try_files $uri $uri/ /index.html', config)
            self.assertIn('error_page 404 /404.html', config)
            self.assertIn('expires 7d', config)

    def test_compose_security_rejects_host_access(self):
        for unsafe in (
            'services:\n  web:\n    privileged: true',
            'services:\n  web:\n    network_mode: host',
            'services:\n  web:\n    volumes:\n      - /var/run/docker.sock:/sock',
            'services:\n  web:\n    devices:\n      - /dev/sda',
        ):
            self.assertTrue(validate_custom_compose(unsafe))

    def test_customer_compose_is_disabled_and_dockerfile_is_read_only(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, 'source')
            stack = os.path.join(root, 'stack')
            os.makedirs(source)
            with open(os.path.join(source, 'compose.yml'), 'w', encoding='utf-8') as handle:
                handle.write('services:\n  web:\n    image: nginx\n    expose: [8080]\n')
            with self.assertRaisesRegex(ValueError, 'not enabled'):
                prepare_custom_docker(stack, source)
            os.unlink(os.path.join(source, 'compose.yml'))
            with open(os.path.join(source, 'Dockerfile'), 'w', encoding='utf-8') as handle:
                handle.write('FROM nginx:1.27-alpine\n')
            prepare_custom_docker(stack, source)
            with open(os.path.join(stack, 'compose.yml'), encoding='utf-8') as handle:
                generated = handle.read()
            self.assertIn('read_only: true', generated)
            self.assertIn('127.0.0.1:', generated)

    def test_detection_distinguishes_frontend_build_and_server_node(self):
        frontend = detect_file_names(['package.json', 'vite.config.ts'], {'devDependencies': {'vite': '^7'}, 'scripts': {'build': 'vite build'}})
        server = detect_file_names(['package.json'], {'dependencies': {'express': '^5'}, 'scripts': {'start': 'node server.js'}})
        self.assertEqual(frontend['recommended'], 'static')
        self.assertEqual(server['recommended'], 'node')

    @mock.patch('runtime_engine.urllib.request.urlopen')
    def test_node_healthcheck_uses_health_endpoint(self, mocked_urlopen):
        mocked_urlopen.return_value.__enter__.return_value.status = 200
        result = healthcheck({'runtime': 'node', 'port': 20000}, timeout=1)
        self.assertTrue(result['ok'])
        self.assertEqual(mocked_urlopen.call_args.args[0], 'http://127.0.0.1:20000/health')

    def test_wordpress_runtime_has_exact_fastcgi_health_and_manual_setup_response(self):
        with tempfile.TemporaryDirectory() as root:
            stack = os.path.join(root, 'stack')
            source = os.path.join(root, 'public_html')
            metadata = prepare_wordpress_runtime(stack, source, port=23456, populate_wordpress=False)
            self.assertEqual(metadata['health_path'], '/myh-runtime-health')
            with open(os.path.join(stack, 'nginx.conf'), encoding='utf-8') as handle:
                config = handle.read()
            self.assertIn('fastcgi_pass wordpress:9000', config)
            self.assertIn('return 503 "WordPress setup required for this site.', config)
            with open(os.path.join(stack, 'compose.yml'), encoding='utf-8') as handle:
                compose = handle.read()
            self.assertIn('MYH_WORDPRESS_POPULATE=0', compose)
            self.assertIn('/var/www/myh-health.php:ro', compose)

    @mock.patch('runtime_registry._installed', return_value=(True, 'ok'))
    def test_registry_requires_successful_e2e(self, _installed):
        with tempfile.TemporaryDirectory() as root:
            config = os.path.join(root, 'config.json')
            health = os.path.join(root, 'health.json')
            with open(config, 'w', encoding='utf-8') as handle:
                json.dump({}, handle)
            with open(health, 'w', encoding='utf-8') as handle:
                json.dump({'generated_at': 'now', 'results': [{'runtime': 'static', 'status': 'AVAILABLE'}]}, handle)
            rows = {row['id']: row for row in runtime_catalog(config, health)}
            self.assertEqual(rows['static']['status'], 'AVAILABLE')
            self.assertEqual(rows['php']['status'], 'NOT_TESTED')


if __name__ == '__main__':
    unittest.main()
