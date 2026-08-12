"""Controlled Docker runtime lifecycle for hosting applications."""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import urllib.error
import urllib.request
import time
from pathlib import Path

SUPPORTED_RUNTIMES = {'static', 'php', 'node', 'python', 'docker', 'wordpress'}
RUNTIME_VERSIONS = {
    'static': ['nginx-alpine'],
    'php': ['8.2', '8.3', '8.4'],
    'node': ['22'],
    'python': ['3.12'],
    'docker': ['nginx-alpine'],
    'wordpress': ['6-php8.3-fpm-alpine'],
}
UNSAFE_COMPOSE_PATTERNS = (
    r'(?im)^\s*privileged\s*:\s*true\s*$', r'(?im)^\s*(network_mode|pid|ipc)\s*:\s*host\s*$',
    r'/var/run/docker\.sock', r'(?im)^\s*devices\s*:', r'(?im)^\s*cap_add\s*:',
    r'(?m)-\s*/(?:\s|:|$)', r'(?m)-\s*/(?:etc|root)(?:\s|:|/)',
)
UNSAFE_COMMAND_PATTERN = re.compile(r'(?i)(?:^|[;&|]\s*)(sudo|docker|mount|umount|nsenter)\b|/var/run/docker\.sock')


def safe_project_name(folder_name: str) -> str:
    value = re.sub(r'[^a-z0-9_-]+', '-', (folder_name or '').lower()).strip('-_')
    if not value:
        raise ValueError('invalid application identifier')
    return f'myh-{value[:48]}'


def allocate_loopback_port(start: int = 20000, end: int = 29999) -> int:
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(('127.0.0.1', port))
            except OSError:
                continue
            return port
    raise RuntimeError('no internal application ports available')


def detect_stack(source_root: str) -> dict:
    root = Path(source_root)
    markers = []
    checks = {
        'docker-compose': ('compose.yml', 'compose.yaml', 'docker-compose.yml', 'docker-compose.yaml'),
        'docker': ('Dockerfile',), 'node': ('package.json',), 'php': ('composer.json', 'index.php'),
        'python': ('requirements.txt', 'pyproject.toml'), 'go': ('go.mod',),
        'java': ('pom.xml', 'build.gradle'), 'dotnet': (), 'ruby': ('Gemfile',),
    }
    for runtime, names in checks.items():
        if any((root / name).exists() for name in names): markers.append(runtime)
    if list(root.glob('*.csproj')) or list(root.glob('*.sln')): markers.append('dotnet')
    if list(root.glob('*.jar')): markers.append('java')
    if not markers and ((root / 'index.html').exists() or (root / 'index.htm').exists()): markers.append('static')
    framework = None
    if (root / 'wp-config.php').exists() or (root / 'wp-content').is_dir(): framework = 'wordpress'
    elif (root / 'artisan').exists(): framework = 'laravel'
    elif (root / 'manage.py').exists(): framework = 'django'
    elif (root / 'vite.config.js').exists() or (root / 'vite.config.ts').exists(): framework = 'vite'
    return {'candidates': sorted(set(markers)), 'framework': framework, 'ambiguous': len(set(markers)) > 1}


def recommend_runtime(markers: dict, package: dict | None = None) -> dict:
    """Recommend, but never select, a runtime from sanitized project metadata."""
    candidates = set(markers.get('candidates') or [])
    framework = markers.get('framework')
    scripts = package.get('scripts', {}) if isinstance(package, dict) and isinstance(package.get('scripts'), dict) else {}
    dependencies = {}
    if isinstance(package, dict):
        dependencies.update(package.get('dependencies') or {})
        dependencies.update(package.get('devDependencies') or {})
    if {'docker', 'docker-compose'} & candidates:
        recommendation, reason = 'docker', 'Dockerfile or Compose configuration found'
    elif 'php' in candidates:
        recommendation, reason = 'php', 'index.php or composer.json found'
    elif 'python' in candidates:
        recommendation, reason = 'python', 'Python dependency metadata found'
    elif 'node' in candidates:
        static_tools = {'vite', 'react', 'vue', '@angular/core', 'svelte'} & set(dependencies)
        server_tools = {'express', '@nestjs/core', 'fastify', 'next', 'nuxt'} & set(dependencies)
        if (framework == 'vite' or static_tools) and not server_tools and 'start' not in scripts:
            recommendation, reason = 'static', 'frontend build tooling found without a server start script'
        else:
            recommendation, reason = 'node', 'server package metadata or start script found'
    elif 'static' in candidates:
        recommendation, reason = 'static', 'index.html found'
    else:
        recommendation, reason = None, 'not enough metadata'
    return {'recommended': recommendation, 'reason': reason, 'requires_confirmation': True, **markers}


def detect_file_names(names: list[str], package: dict | None = None) -> dict:
    basenames = {Path(str(name).replace('\\', '/')).name for name in names[:500]}
    candidates = []
    for runtime, markers in {
        'docker-compose': {'compose.yml', 'compose.yaml', 'docker-compose.yml', 'docker-compose.yaml'},
        'docker': {'Dockerfile'}, 'node': {'package.json'}, 'php': {'composer.json', 'index.php'},
        'python': {'requirements.txt', 'pyproject.toml', 'Pipfile'},
    }.items():
        if basenames & markers:
            candidates.append(runtime)
    if not candidates and basenames & {'index.html', 'index.htm'}:
        candidates.append('static')
    framework = None
    if {'wp-config.php'} & basenames or 'wp-content' in basenames:
        framework = 'wordpress'
    elif 'artisan' in basenames:
        framework = 'laravel'
    elif 'manage.py' in basenames:
        framework = 'django'
    elif {'vite.config.js', 'vite.config.ts'} & basenames:
        framework = 'vite'
    markers = {'candidates': sorted(set(candidates)), 'framework': framework, 'ambiguous': len(set(candidates)) > 1}
    return recommend_runtime(markers, package)


def infer_runtime_commands(source_root: str, runtime: str) -> dict:
    root = Path(source_root)
    result = {'install_command': '', 'build_command': '', 'start_command': ''}
    if runtime == 'node' and (root / 'package.json').is_file():
        try:
            package = json.loads((root / 'package.json').read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            package = {}
        scripts = package.get('scripts') if isinstance(package.get('scripts'), dict) else {}
        if (root / 'pnpm-lock.yaml').exists(): manager, install = 'pnpm', 'corepack enable && pnpm install --frozen-lockfile'
        elif (root / 'yarn.lock').exists(): manager, install = 'yarn', 'corepack enable && yarn install --frozen-lockfile'
        else: manager, install = 'npm', ('npm ci' if (root / 'package-lock.json').exists() else 'npm install')
        result['install_command'] = install
        if 'build' in scripts: result['build_command'] = f'{manager} run build'
        if 'start' in scripts: result['start_command'] = f'{manager} start'
    elif runtime == 'python':
        if (root / 'requirements.txt').exists(): result['install_command'] = 'pip install --target /tmp/deps -r requirements.txt'
        if (root / 'manage.py').exists(): result['start_command'] = 'gunicorn --bind 0.0.0.0:8080 --workers 2 project.wsgi:application'
    return result


def validate_custom_compose(text: str) -> list[str]:
    return [pattern for pattern in UNSAFE_COMPOSE_PATTERNS if re.search(pattern, text or '')]


def prepare_custom_docker(stack_root: str, source_root: str, port: int | None = None) -> dict:
    stack, source = Path(stack_root).resolve(), Path(source_root).resolve()
    stack.mkdir(parents=True, exist_ok=True)
    source.mkdir(parents=True, exist_ok=True)
    try:
        source.chmod(0o2750)
    except PermissionError:
        pass
    try:
        os.chown(source, -1, 33)
    except PermissionError:
        pass
    compose_source = next((source / name for name in ('compose.yml', 'compose.yaml', 'docker-compose.yml', 'docker-compose.yaml') if (source / name).is_file()), None)
    port = int(port or allocate_loopback_port()); project = safe_project_name(stack.name)
    if compose_source:
        raise ValueError('customer Docker Compose is not enabled; deploy a single validated Dockerfile')
    if (source / 'Dockerfile').is_file():
        compose = f'''services:
  web:
    build: {{context: "{source}", dockerfile: Dockerfile}}
    restart: unless-stopped
    ports: ["127.0.0.1:{port}:8080"]
    networks: [app-internal, hosting-databases]
    security_opt: ["no-new-privileges:true"]
    cap_drop: [ALL]
    cap_add: [CHOWN, SETGID, SETUID]
    read_only: true
    tmpfs: ["/tmp:exec,mode=1777", "/run:mode=0755", "/var/cache/nginx:mode=0755"]
    mem_limit: 512m
    cpus: 1.0
    pids_limit: 192
    logging: {{driver: json-file, options: {{max-size: "10m", max-file: "3"}}}}
networks:
  app-internal: {{driver: bridge}}
  hosting-databases: {{external: true}}
'''
        _write(stack / 'compose.yml', compose); compose_files = [str(stack / 'compose.yml')]
    else:
        return prepare_runtime(str(stack), str(source), 'docker', port=port)
    metadata = {'runtime':'docker','version':'custom','port':port,'project':project,'source_root':str(source),'status':'configured','compose_files':compose_files,'project_directory':str(source)}
    _write(stack / 'runtime.json', json.dumps(metadata, indent=2) + '\n', 0o600)
    return metadata


def prepare_wordpress_runtime(stack_root: str, source_root: str, port: int | None = None) -> dict:
    stack, source = Path(stack_root).resolve(), Path(source_root).resolve(); stack.mkdir(parents=True, exist_ok=True); source.mkdir(parents=True, exist_ok=True)
    source.chmod(0o2770); source_gid = source.stat().st_gid; port = int(port or allocate_loopback_port()); project = safe_project_name(stack.name)
    env_file = stack / 'env.list'
    if not env_file.exists(): _write(env_file, '', 0o600)
    _write(stack / 'nginx.conf', 'server { listen 8080; root /var/www/html; index index.php index.html; client_max_body_size 64m; location / { try_files $uri $uri/ /index.php?$args; } location ~ \\.php$ { include fastcgi_params; fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name; fastcgi_pass wordpress:9000; } }\n', 0o644)
    compose = f'''services:
  web:
    image: nginx:1.27-alpine
    user: "101:101"
    restart: unless-stopped
    ports: ["127.0.0.1:{port}:8080"]
    networks: [app-internal]
    security_opt: ["no-new-privileges:true"]
    cap_drop: [ALL]
    group_add: ["{source_gid}", "33"]
    read_only: true
    volumes: ["{source}:/var/www/html:ro", "{stack / 'nginx.conf'}:/etc/nginx/conf.d/default.conf:ro"]
    tmpfs: ["/var/cache/nginx:uid=101,gid=101,mode=0750", "/var/run:uid=101,gid=101,mode=0750"]
    mem_limit: 256m
    cpus: 0.75
    pids_limit: 128
    depends_on: [wordpress]
  wordpress:
    image: wordpress:6-php8.3-fpm-alpine
    user: "82:82"
    restart: unless-stopped
    networks: [app-internal, hosting-databases]
    security_opt: ["no-new-privileges:true"]
    cap_drop: [ALL]
    group_add: ["{source_gid}", "33"]
    env_file: ["{env_file}"]
    volumes: ["{source}:/var/www/html"]
    mem_limit: 384m
    cpus: 0.75
    pids_limit: 128
networks:
  app-internal: {{driver: bridge}}
  hosting-databases: {{external: true}}
'''
    _write(stack / 'compose.yml', compose)
    metadata={'runtime':'wordpress','version':'6-php8.3-fpm-alpine','port':port,'project':project,'source_root':str(source),'status':'configured'}
    _write(stack / 'runtime.json',json.dumps(metadata,indent=2)+'\n',0o600); return metadata


def _write(path: Path, content: str, mode: int = 0o640) -> None:
    path.write_text(content, encoding='utf-8')
    path.chmod(mode)
    try:
        os.chown(path, -1, 33)
    except PermissionError:
        pass


def _safe_command(value: str | None, default: str = '') -> str:
    command = (value or default).strip()
    if len(command) > 500 or '\n' in command or '\r' in command or UNSAFE_COMMAND_PATTERN.search(command):
        raise ValueError('unsafe runtime command')
    return command


def validate_runtime_commands(*commands: str | None) -> bool:
    try:
        for command in commands: _safe_command(command)
        return True
    except ValueError:
        return False


def prepare_runtime(stack_root: str, source_root: str, runtime: str, version: str | None = None, port: int | None = None,
                    install_command: str | None = None, build_command: str | None = None, start_command: str | None = None,
                    spa_enabled: bool = False) -> dict:
    if runtime not in SUPPORTED_RUNTIMES: raise ValueError('unsupported runtime')
    allowed_versions = RUNTIME_VERSIONS[runtime]
    version = version or allowed_versions[0]
    if version not in allowed_versions: raise ValueError('unsupported runtime version')
    stack, source = Path(stack_root).resolve(), Path(source_root).resolve()
    stack.mkdir(parents=True, exist_ok=True); source.mkdir(parents=True, exist_ok=True)
    try:
        source.chmod(0o2750)
    except PermissionError:
        pass
    try:
        os.chown(source, -1, 33)
    except PermissionError:
        pass
    source_gid = source.stat().st_gid
    port = int(port or allocate_loopback_port())
    if not 1024 <= port <= 65535: raise ValueError('invalid internal port')
    project = safe_project_name(stack.name)
    env_file = stack / 'env.list'
    if not env_file.exists():
        _write(env_file, '', 0o600)

    database_network = ', hosting-databases' if runtime in {'php', 'node', 'python'} else ''
    common = '''    restart: unless-stopped
    ports: ["127.0.0.1:{port}:8080"]
    networks: [app-internal{database_network}]
    security_opt: ["no-new-privileges:true"]
    cap_drop: [ALL]
    group_add: ["{source_gid}", "33"]
    env_file: ["{env_file}"]
    mem_limit: 256m
    cpus: 0.75
    pids_limit: 128
    logging:
      driver: json-file
      options: {{max-size: "10m", max-file: "3"}}
'''.format(port=port, source_gid=source_gid, env_file=env_file, database_network=database_network)
    if runtime in {'static', 'docker'}:
        if not (source / 'index.html').exists(): _write(source / 'index.html', '<h1>Static runtime OK</h1>\n', 0o640)
        compose = f'''services:
  web:
    image: nginx:1.27-alpine
{common}    user: "101:101"
    read_only: true
    volumes: ["{source}:/usr/share/nginx/html:ro"]
    tmpfs: ["/var/cache/nginx:uid=101,gid=101,mode=0750", "/var/run:uid=101,gid=101,mode=0750"]
    healthcheck: {{test: ["CMD-SHELL", "wget -qO- http://127.0.0.1:8080/ >/dev/null"], interval: 10s, timeout: 3s, retries: 6}}
'''
        fallback = '/index.html' if spa_enabled else '=404'
        _write(stack / 'default.conf', f'''server {{
    listen 8080;
    root /usr/share/nginx/html;
    index index.html index.htm;
    gzip on;
    gzip_types text/css application/javascript application/json image/svg+xml;
    error_page 404 /404.html;
    location / {{ try_files $uri $uri/ {fallback}; }}
    location ~* \\.(?:css|js|png|jpg|jpeg|gif|svg|webp|woff2?)$ {{ expires 7d; add_header Cache-Control "public, max-age=604800"; try_files $uri =404; }}
}}\n''', 0o644)
        compose = compose.replace(f'    volumes: ["{source}:/usr/share/nginx/html:ro"]', f'    volumes: ["{source}:/usr/share/nginx/html:ro", "{stack / "default.conf"}:/etc/nginx/conf.d/default.conf:ro"]')
    elif runtime == 'php':
        if not (source / 'index.php').exists(): _write(source / 'index.php', '<?php echo "PHP OK"; ?>\n')
        _write(stack / 'nginx.conf', 'server { listen 8080; root /var/www/html; index index.php index.html; location / { try_files $uri $uri/ /index.php?$query_string; } location ~ \\.php$ { include fastcgi_params; fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name; fastcgi_pass php:9000; } }\n', 0o644)
        compose = f'''services:
  web:
    image: nginx:1.27-alpine
{common}    user: "101:101"
    read_only: true
    volumes: ["{source}:/var/www/html:ro", "{stack / 'nginx.conf'}:/etc/nginx/conf.d/default.conf:ro"]
    tmpfs: ["/var/cache/nginx:uid=101,gid=101,mode=0750", "/var/run:uid=101,gid=101,mode=0750"]
    depends_on: [php]
    healthcheck: {{test: ["CMD-SHELL", "wget -qO- http://127.0.0.1:8080/ >/dev/null"], interval: 10s, timeout: 3s, retries: 8}}
  php:
    image: myh-stack-php:{version}
    user: "82:82"
    restart: unless-stopped
    networks: [app-internal, hosting-databases]
    env_file: ["{env_file}"]
    security_opt: ["no-new-privileges:true"]
    cap_drop: [ALL]
    group_add: ["{source_gid}", "33"]
    read_only: true
    tmpfs: ["/tmp"]
    volumes: ["{source}:/var/www/html:ro"]
    mem_limit: 256m
    cpus: 0.75
    pids_limit: 128
'''
    elif runtime == 'node':
        if not (source / 'server.js').exists(): _write(source / 'server.js', "const http=require('http');http.createServer((q,r)=>{r.end(q.url==='/health'?'ok':'Node OK')}).listen(8080,'0.0.0.0');\n")
        install_command = _safe_command(install_command)
        build_command = _safe_command(build_command)
        start_command = _safe_command(start_command, 'node server.js')
        lifecycle = '; '.join(item for item in (install_command, build_command, f'exec {start_command}') if item)
        node_boot = f'rm -rf /tmp/app; mkdir /tmp/app; cp -R /workspace/. /tmp/app/; cd /tmp/app; export HOME=/tmp; {lifecycle}'
        compose = f'''services:
  web:
    image: node:{version}-alpine
{common}    user: "node"
    read_only: true
    working_dir: /tmp
    command: ["sh", "-lc", {json.dumps(node_boot)}]
    volumes: ["{source}:/workspace:ro"]
    tmpfs: ["/tmp:exec,uid=1000,gid=1000,mode=0750"]
    healthcheck: {{test: ["CMD-SHELL", "wget -qO- http://127.0.0.1:8080/health >/dev/null"], interval: 10s, timeout: 3s, retries: 8}}
'''
    else:
        if not (source / 'app.py').exists(): _write(source / 'app.py', "def app(environ, start_response):\n    start_response('200 OK', [('Content-Type', 'text/plain')])\n    return [b'Python OK']\n")
        install_command = _safe_command(install_command)
        build_command = _safe_command(build_command)
        start_command = _safe_command(start_command, 'gunicorn --bind 0.0.0.0:8080 --workers 2 --access-logfile - --error-logfile - app:app')
        python_install = install_command or ('pip install --target /tmp/deps -r requirements.txt' if (source / 'requirements.txt').exists() else '')
        lifecycle = '; '.join(item for item in (python_install, build_command, f'PYTHONPATH=/tmp/deps exec {start_command}') if item)
        python_boot = f'rm -rf /tmp/app; mkdir /tmp/app; cp -R /workspace/. /tmp/app/; cd /tmp/app; export HOME=/tmp; {lifecycle}'
        compose = f'''services:
  web:
    image: myh-stack-python-web:3.12
{common}    user: "65534:65534"
    read_only: true
    working_dir: /tmp
    command: ["sh", "-lc", {json.dumps(python_boot)}]
    volumes: ["{source}:/workspace:ro"]
    tmpfs: ["/tmp:exec,uid=65534,gid=65534,mode=0750"]
    healthcheck: {{test: ["CMD-SHELL", "wget -qO- http://127.0.0.1:8080/ >/dev/null"], interval: 10s, timeout: 3s, retries: 8}}
'''
    compose += '''networks:
  app-internal: {driver: bridge}
  hosting-databases: {external: true}
'''
    _write(stack / 'compose.yml', compose)
    metadata = {'runtime': runtime, 'version': version, 'port': port, 'project': project, 'source_root': str(source), 'status': 'configured',
                'install_command': install_command or '', 'build_command': build_command or '', 'start_command': start_command or '',
                'spa_enabled': bool(spa_enabled)}
    _write(stack / 'runtime.json', json.dumps(metadata, indent=2) + '\n', 0o600)
    return metadata


def compose_action(stack_root: str, action: str, timeout: int = 180) -> tuple[int, str]:
    stack = Path(stack_root).resolve(); metadata = json.loads((stack / 'runtime.json').read_text(encoding='utf-8'))
    commands = {'start': ['up', '-d', '--build', '--force-recreate'], 'stop': ['stop'], 'restart': ['restart'], 'delete': ['down', '--remove-orphans']}
    if action not in commands: raise ValueError('unsupported lifecycle action')
    compose_files = metadata.get('compose_files') or [str(stack / 'compose.yml')]
    command = ['docker', 'compose', '-p', metadata['project']]
    for compose_file in compose_files: command.extend(['-f', compose_file])
    if metadata.get('project_directory'): command.extend(['--project-directory', metadata['project_directory']])
    proc = subprocess.run([*command, *commands[action]], capture_output=True, text=True, timeout=timeout, check=False)
    return proc.returncode, ((proc.stdout or '') + '\n' + (proc.stderr or ''))[-8000:]


def healthcheck(metadata: dict, timeout: int = 8) -> dict:
    deadline = time.monotonic() + timeout
    last_error = ''
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{int(metadata['port'])}/", timeout=min(2, timeout)) as response:
                return {'ok': 200 <= response.status < 400, 'status': response.status}
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            last_error = str(exc)[:160]
            time.sleep(0.25)
    return {'ok': False, 'status': None, 'error': last_error}
