from flask import Flask, render_template, request, redirect, url_for, session, send_from_directory, send_file, abort, flash, jsonify, g, has_request_context
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from functools import wraps
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError
import json
import psutil
import os
import zipfile
import subprocess
import re
import secrets
import hashlib
import time
import argparse
import socket
import urllib.request
import urllib.error
import smtplib
import string
import threading
import traceback
from collections import defaultdict, deque
from datetime import datetime

app = Flask(__name__)
panel_secret = os.environ.get('HOSTING_PANEL_SECRET')
if not panel_secret or len(panel_secret) < 32:
    raise RuntimeError('HOSTING_PANEL_SECRET must be set and at least 32 characters long')
app.config['SECRET_KEY'] = panel_secret
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(app.root_path, 'instance', 'hosting.db')}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'user_sites')
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=1800,
    PREFERRED_URL_SCHEME='https',
    TRUSTED_HOSTS=['myh.guru', '.myh.guru', 'localhost', '127.0.0.1'],
)
db = SQLAlchemy(app)
login_attempts = defaultdict(deque)
LOGIN_WINDOW_SECONDS = 300
LOGIN_MAX_IP_ATTEMPTS = 10
LOGIN_MAX_ACCOUNT_ATTEMPTS = 6
CLOUDFLARE_ZONE_NAME = os.environ.get('CLOUDFLARE_ZONE_NAME', 'myh.guru')
CLOUDFLARE_TOKEN_FILE = os.environ.get('CLOUDFLARE_TOKEN_FILE', '/tmp/.cf_token')
CLOUDFLARE_LOCAL_TOKEN_FILE = os.path.join(app.instance_path, 'cloudflare_api_token')
CLOUDFLARE_LOCAL_ZONE_FILE = os.path.join(app.instance_path, 'cloudflare_zone_name')
APP_VERSION = os.environ.get('HOSTING_PANEL_VERSION', '1.5.0')
PUBLIC_HOME_PREFS_FILE = os.path.join(app.instance_path, 'public_home_prefs.json')
DEFAULT_PUBLIC_HOME_PREFS = {
    'show_cpu': True,
    'show_ram': True,
    'private_mode': False,
}
REQUIRED_CORE_TABLES = {
    'user',
    'site',
    'audit_log',
    'job_task',
    'agent_node',
    'plugin_module',
    'deployment_event',
}
_db_repair_lock = threading.Lock()
_db_ready = False
SUPPORTED_LANGUAGES = {'uk': 'Українська', 'en': 'English'}
DEFAULT_LANGUAGE = 'uk'
TRANSLATIONS = {
    'uk': {
        'site_owner_required': 'Оберіть активного власника сайту.',
        'subdomain_taken': 'Такий піддомен уже використовується.',
        'invalid_site_name': 'Назва сайту може містити лише латинські літери, цифри та дефіс.',
        'site_created': 'Сайт створено.',
        'invalid_username': 'Логін: 3–32 символи, латиниця, цифри, _ або -.',
        'invalid_user_profile': 'Заповніть ім’я, прізвище та коректний email.',
        'password_short': 'Пароль повинен містити щонайменше 12 символів.',
        'quota_invalid': 'Квота повинна бути від 64 MB до 51200 MB (50 GB).',
        'user_exists': 'Користувач із таким логіном або email вже існує.',
        'user_created': 'Користувача створено.',
        'user_profile_updated': 'Профіль оновлено.',
        'password_reset': 'Новий тимчасовий пароль створено.',
        'role_updated': 'Роль користувача оновлено.',
        'user_deleted': 'Користувача видалено.',
        'backup_queued': 'Резервну копію поставлено в чергу.',
        'backup_created': 'Резервну копію створено.',
        'restore_queued': 'Відновлення поставлено в чергу.',
        'restore_completed': 'Сайт відновлено з резервної копії.',
        'invalid_directory_name': 'Некоректна назва каталогу.',
        'directory_created': 'Каталог створено.',
        'file_saved': 'Файл збережено.',
        'file_exists': 'Файл або каталог із такою назвою вже існує.',
        'files_uploaded': 'Файлів завантажено.',
        'invalid_domain': 'Некоректний домен.',
        'domain_taken': 'Цей домен уже прив’язаний до іншого сайту.',
        'domain_saved': 'Домен збережено. Налаштуйте DNS CNAME на myh.guru.',
        'module_required': 'Slug і назва обов’язкові.',
        'module_exists': 'Модуль із таким slug уже існує.',
        'module_created': 'Модуль додано до registry.',
        'api_forbidden': 'Доступ заборонено.',
        'api_unauthorized': 'Потрібна авторизація.',
        'invalid_command': 'Команда не дозволена.',
        'no_data': 'Немає даних.',
    },
    'en': {
        'site_owner_required': 'Please choose an active site owner.',
        'subdomain_taken': 'This subdomain is already in use.',
        'invalid_site_name': 'The site name can contain only Latin letters, numbers, and hyphens.',
        'site_created': 'Site created successfully.',
        'invalid_username': 'Username: 3–32 characters, Latin letters, digits, _ or -.',
        'invalid_user_profile': 'Please fill in the first name, last name, and a valid email address.',
        'password_short': 'The password must be at least 12 characters long.',
        'quota_invalid': 'Quota must be between 64 MB and 51200 MB (50 GB).',
        'user_exists': 'A user with this username or email already exists.',
        'user_created': 'User created successfully.',
        'user_profile_updated': 'Profile updated successfully.',
        'password_reset': 'A temporary password has been generated.',
        'role_updated': 'User role updated successfully.',
        'user_deleted': 'User deleted successfully.',
        'backup_queued': 'Backup queued successfully.',
        'backup_created': 'Backup created successfully.',
        'restore_queued': 'Restore queued successfully.',
        'restore_completed': 'The site was restored from backup.',
        'invalid_directory_name': 'Invalid directory name.',
        'directory_created': 'Directory created.',
        'file_saved': 'File saved.',
        'file_exists': 'A file or directory with that name already exists.',
        'files_uploaded': 'Files uploaded.',
        'invalid_domain': 'Invalid domain.',
        'domain_taken': 'This domain is already linked to another site.',
        'domain_saved': 'Domain saved. Configure a DNS CNAME for myh.guru.',
        'module_required': 'Slug and name are required.',
        'module_exists': 'A module with this slug already exists.',
        'module_created': 'Module added to the registry.',
        'api_forbidden': 'Forbidden.',
        'api_unauthorized': 'Authentication required.',
        'invalid_command': 'Command is not allowed.',
        'no_data': 'No data.',
    },
}


def resolve_language(lang=None):
    if lang in SUPPORTED_LANGUAGES:
        return lang
    if has_request_context():
        selected = session.get('language') or request.args.get('lang') or DEFAULT_LANGUAGE
        if selected in SUPPORTED_LANGUAGES:
            return selected
    return DEFAULT_LANGUAGE


def translate(key, lang=None):
    selected = resolve_language(lang)
    return TRANSLATIONS.get(selected, TRANSLATIONS[DEFAULT_LANGUAGE]).get(key, TRANSLATIONS[DEFAULT_LANGUAGE].get(key, key))

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    middle_name = db.Column(db.String(80), nullable=True)
    phone = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)   # Чи є розробником/адміном (Крок 1)
    is_banned = db.Column(db.Boolean, default=False)  # Статус блокування користувача (Крок 1)
    quota_mb = db.Column(db.Integer, nullable=False, default=51200)
    must_change_password = db.Column(db.Boolean, nullable=False, default=False)
    last_login_at = db.Column(db.DateTime, nullable=True)
    sites = db.relationship('Site', backref='owner', lazy=True)

class Site(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    folder_name = db.Column(db.String(100), unique=True, nullable=False)
    php_version = db.Column(db.String(20), nullable=False, default='8.2')
    is_banned = db.Column(db.Boolean, default=False)  # Статус блокування конкретного сайту (Крок 1)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    custom_domain = db.Column(db.String(255), nullable=True)
    webhook_secret = db.Column(db.String(255), nullable=True)
    webhook_branch = db.Column(db.String(120), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=True)
    username = db.Column(db.String(80), nullable=False, default='system')
    action = db.Column(db.String(80), nullable=False)
    detail = db.Column(db.String(500), nullable=False, default='')
    ip_address = db.Column(db.String(64), nullable=False, default='')
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)


class JobTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    job_type = db.Column(db.String(80), nullable=False)
    target = db.Column(db.String(120), nullable=True)
    payload_json = db.Column(db.Text, nullable=False, default='{}')
    status = db.Column(db.String(20), nullable=False, default='pending')
    progress = db.Column(db.Integer, nullable=False, default=0)
    message = db.Column(db.String(255), nullable=False, default='queued')
    result_json = db.Column(db.Text, nullable=False, default='{}')
    created_by = db.Column(db.String(80), nullable=False, default='system')
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)


class AgentNode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    host_ip = db.Column(db.String(64), nullable=False, default='')
    platform = db.Column(db.String(80), nullable=False, default='linux')
    role = db.Column(db.String(80), nullable=False, default='general')
    version = db.Column(db.String(40), nullable=False, default='1.0.0')
    capabilities_json = db.Column(db.Text, nullable=False, default='[]')
    status = db.Column(db.String(20), nullable=False, default='offline')
    last_seen_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


class PluginModule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(120), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(80), nullable=False, default='platform')
    description = db.Column(db.String(500), nullable=False, default='')
    version = db.Column(db.String(40), nullable=False, default='1.0.0')
    source_url = db.Column(db.String(255), nullable=False, default='')
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    built_in = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


class DeploymentEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    site_name = db.Column(db.String(120), nullable=False, default='')
    deploy_mode = db.Column(db.String(40), nullable=False, default='zip')
    status = db.Column(db.String(20), nullable=False, default='success')
    detail = db.Column(db.String(500), nullable=False, default='')
    repo_url = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)


BUILTIN_MODULES = [
    {'slug': 'developer-dashboard', 'name': 'Developer Dashboard', 'category': 'ui', 'description': 'Core administrative workspace for panels, metrics, and operations.', 'version': '1.0.0'},
    {'slug': 'dns-ssl-center', 'name': 'DNS & SSL Center', 'category': 'cloudflare', 'description': 'Cloudflare DNS and edge security control plane.', 'version': '1.0.0'},
    {'slug': 'infrastructure-center', 'name': 'Infrastructure Center', 'category': 'ops', 'description': 'Topology, agents, services, and platform posture view.', 'version': '1.0.0'},
    {'slug': 'job-queue', 'name': 'Job Queue', 'category': 'orchestration', 'description': 'Background job execution and progress tracking.', 'version': '1.0.0'},
    {'slug': 'watchdog', 'name': 'Platform Watchdog', 'category': 'sre', 'description': 'Self-healing service and container monitoring timer.', 'version': '1.0.0'},
    {'slug': 'site-manager', 'name': 'Site Manager', 'category': 'hosting', 'description': 'File, domain, and backup management for hosted sites.', 'version': '1.0.0'},
]

def ensure_database_schema(force=False):
    global _db_ready
    if _db_ready and not force:
        return
    with _db_repair_lock:
        if _db_ready and not force:
            return

        db.create_all()
        inspector = inspect(db.engine)
        table_names = set(inspector.get_table_names())

        if 'user' in table_names:
            user_columns = {column['name'] for column in inspector.get_columns('user')}
        else:
            user_columns = set()
        if 'site' in table_names:
            site_columns = {column['name'] for column in inspector.get_columns('site')}
        else:
            site_columns = set()

        migrations = [
            ('user', 'quota_mb', "ALTER TABLE user ADD COLUMN quota_mb INTEGER NOT NULL DEFAULT 51200", user_columns),
            ('user', 'must_change_password', "ALTER TABLE user ADD COLUMN must_change_password BOOLEAN NOT NULL DEFAULT 0", user_columns),
            ('user', 'last_login_at', "ALTER TABLE user ADD COLUMN last_login_at DATETIME", user_columns),
            ('site', 'php_version', "ALTER TABLE site ADD COLUMN php_version VARCHAR(20) NOT NULL DEFAULT '8.2'", site_columns),
            ('site', 'custom_domain', "ALTER TABLE site ADD COLUMN custom_domain VARCHAR(255)", site_columns),
            ('site', 'webhook_secret', "ALTER TABLE site ADD COLUMN webhook_secret VARCHAR(255)", site_columns),
            ('site', 'webhook_branch', "ALTER TABLE site ADD COLUMN webhook_branch VARCHAR(120)", site_columns),
            ('site', 'created_at', "ALTER TABLE site ADD COLUMN created_at DATETIME", site_columns),
        ]

        with db.engine.begin() as conn:
            for table_name, column_name, statement, existing in migrations:
                if table_name in table_names and column_name not in existing:
                    conn.execute(text(statement))
            if 'user' in table_names and 'quota_mb' in user_columns:
                conn.execute(text("UPDATE user SET quota_mb = 51200 WHERE quota_mb = 256"))

        inspector = inspect(db.engine)
        job_tables = {table_name for table_name in inspector.get_table_names()}
        with db.engine.begin() as conn:
            if 'job_task' not in job_tables:
                conn.execute(text("""
                    CREATE TABLE job_task (
                        id INTEGER PRIMARY KEY,
                        job_type VARCHAR(80) NOT NULL,
                        target VARCHAR(120),
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        status VARCHAR(20) NOT NULL DEFAULT 'pending',
                        progress INTEGER NOT NULL DEFAULT 0,
                        message VARCHAR(255) NOT NULL DEFAULT 'queued',
                        result_json TEXT NOT NULL DEFAULT '{}',
                        created_by VARCHAR(80) NOT NULL DEFAULT 'system',
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL,
                        started_at DATETIME,
                        finished_at DATETIME
                    )
                """))
            if 'agent_node' not in job_tables:
                conn.execute(text("""
                    CREATE TABLE agent_node (
                        id INTEGER PRIMARY KEY,
                        name VARCHAR(120) UNIQUE NOT NULL,
                        host_ip VARCHAR(64) NOT NULL DEFAULT '',
                        platform VARCHAR(80) NOT NULL DEFAULT 'linux',
                        role VARCHAR(80) NOT NULL DEFAULT 'general',
                        version VARCHAR(40) NOT NULL DEFAULT '1.0.0',
                        capabilities_json TEXT NOT NULL DEFAULT '[]',
                        status VARCHAR(20) NOT NULL DEFAULT 'offline',
                        last_seen_at DATETIME,
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL
                    )
                """))
            if 'plugin_module' not in job_tables:
                conn.execute(text("""
                    CREATE TABLE plugin_module (
                        id INTEGER PRIMARY KEY,
                        slug VARCHAR(120) UNIQUE NOT NULL,
                        name VARCHAR(120) NOT NULL,
                        category VARCHAR(80) NOT NULL DEFAULT 'platform',
                        description VARCHAR(500) NOT NULL DEFAULT '',
                        version VARCHAR(40) NOT NULL DEFAULT '1.0.0',
                        source_url VARCHAR(255) NOT NULL DEFAULT '',
                        enabled BOOLEAN NOT NULL DEFAULT 1,
                        built_in BOOLEAN NOT NULL DEFAULT 0,
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL
                    )
                """))

        existing_modules = {module.slug for module in PluginModule.query.all()}
        seeded = False
        for module_data in BUILTIN_MODULES:
            if module_data['slug'] not in existing_modules:
                db.session.add(PluginModule(
                    slug=module_data['slug'],
                    name=module_data['name'],
                    category=module_data['category'],
                    description=module_data['description'],
                    version=module_data['version'],
                    source_url='',
                    enabled=True,
                    built_in=True,
                ))
                seeded = True
        if seeded:
            db.session.commit()
        _db_ready = True


with app.app_context():
    ensure_database_schema(force=True)


def ensure_default_admin_user():
    if User.query.count() > 0:
        return None
    username = (os.environ.get('HOSTING_PANEL_ADMIN_USERNAME') or 'developer').strip() or 'developer'
    password = (os.environ.get('HOSTING_PANEL_ADMIN_PASSWORD') or '').strip()
    if not password:
        password = secrets.token_urlsafe(16)
    user = User(
        username=username,
        first_name='Developer',
        last_name='Admin',
        phone='0000000000',
        email=f'{username}@localhost',
        password=generate_password_hash(password),
        is_admin=True,
        must_change_password=True,
    )
    db.session.add(user)
    db.session.commit()
    print(f'Bootstrap admin user created: {username}/{password}', flush=True)
    return user


def create_or_reset_admin_user(username='developer', password=None, email=None, force=False):
    if password is None:
        password = secrets.token_urlsafe(16)
    if email is None:
        email = f'{username}@localhost'
    with app.app_context():
        user = User.query.filter_by(username=username).first()
        if user is None:
            user = User(username=username, first_name='Developer', last_name='Admin', phone='0000000000', email=email, password=generate_password_hash(password), is_admin=True, must_change_password=True)
            db.session.add(user)
            print(f'Created admin user: {username}/{password}', flush=True)
        else:
            if force or not user.is_admin:
                user.first_name = 'Developer'
                user.last_name = 'Admin'
                user.phone = '0000000000'
                user.email = email
                user.password = generate_password_hash(password)
                user.is_admin = True
                user.must_change_password = True
                user.is_banned = False
                print(f'Reset admin user: {username}/{password}', flush=True)
            else:
                print(f'Admin user already exists: {username}', flush=True)
        db.session.commit()
        return user, password


with app.app_context():
    ensure_default_admin_user()


def get_current_language():
    lang = session.get('language') or request.args.get('lang') or DEFAULT_LANGUAGE
    if lang not in SUPPORTED_LANGUAGES:
        lang = DEFAULT_LANGUAGE
    session['language'] = lang
    return lang


@app.context_processor
def inject_user():
    user = None
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_urlsafe(32)
    return {
        'current_user': user,
        'is_admin_user': bool(user and user.is_admin),
        'csrf_token': session['_csrf_token'],
        'csp_nonce': getattr(g, 'csp_nonce', ''),
        'current_language': get_current_language(),
        'supported_languages': SUPPORTED_LANGUAGES,
        'app_version': APP_VERSION,
    }


@app.before_request
def prepare_security_context():
    g.csp_nonce = secrets.token_urlsafe(16)


@app.before_request
def ensure_database_health():
    try:
        db.session.execute(text("SELECT id FROM user LIMIT 1"))
    except OperationalError:
        db.session.rollback()
        ensure_database_schema(force=True)


@app.before_request
def validate_csrf():
    if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        if request.path.startswith('/api/'):
            return
        supplied = request.form.get('_csrf_token') or request.headers.get('X-CSRF-Token')
        expected = session.get('_csrf_token')
        if not expected or not supplied or not secrets.compare_digest(expected, supplied):
            abort(400, 'Недійсний CSRF-токен')


@app.after_request
def security_headers(response):
    nonce = getattr(g, 'csp_nonce', '')
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    response.headers['Cross-Origin-Opener-Policy'] = 'same-origin'
    response.headers['Cross-Origin-Resource-Policy'] = 'same-origin'
    response.headers['Origin-Agent-Cluster'] = '?1'
    if request.endpoint == 'view_site':
        response.headers['Content-Security-Policy'] = "sandbox; default-src 'self' data: blob:; img-src 'self' data: blob:"
    else:
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            f"style-src 'self' 'unsafe-inline' 'nonce-{nonce}'; "
            f"script-src 'self' 'nonce-{nonce}'; "
            "img-src 'self' data:; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
        )
    return response


def get_server_metrics():
    cpu_percent = psutil.cpu_percent(interval=None)
    load_avg = os.getloadavg()[0] if hasattr(os, 'getloadavg') else 0
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    return {
        'cpu': round(cpu_percent, 1),
        'load': round(load_avg, 2),
        'ram_total_mb': round(memory.total / (1024 * 1024), 1),
        'ram_used_mb': round(memory.used / (1024 * 1024), 1),
        'ram_free_mb': round(memory.available / (1024 * 1024), 1),
        'ram_percent': round(memory.percent, 1),
        'disk_total_gb': round(disk.total / (1024 ** 3), 1),
        'disk_used_gb': round(disk.used / (1024 ** 3), 1),
        'disk_free_gb': round(disk.free / (1024 ** 3), 1),
        'disk_percent': round(disk.percent, 1),
        'timestamp': datetime.now().strftime('%H:%M:%S')
    }


def check_database_health():
    try:
        db.session.execute(text("SELECT 1"))
        inspector = inspect(db.engine)
        table_names = set(inspector.get_table_names())
        missing = sorted(REQUIRED_CORE_TABLES - table_names)
        if missing:
            return False, f"missing tables: {', '.join(missing)}"
        return True, 'ok'
    except Exception as exc:
        db.session.rollback()
        return False, str(exc)


def get_service_status(service_name):
    commands = []
    if os.path.exists('/bin/systemctl'):
        commands.append(['systemctl', 'is-active', service_name])
    if os.path.exists('/usr/sbin/service'):
        commands.append(['service', service_name, 'status'])
    if os.path.exists('/etc/init.d/' + service_name):
        commands.append([f'/etc/init.d/{service_name}', 'status'])

    for cmd in commands:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                output = (result.stdout or result.stderr).strip() or 'active'
                return {'name': service_name, 'active': True, 'detail': output}
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    return {'name': service_name, 'active': False, 'detail': 'Unavailable'}


def normalize_public_home_prefs(raw):
    data = raw if isinstance(raw, dict) else {}
    return {
        'show_cpu': bool(data.get('show_cpu', True)),
        'show_ram': bool(data.get('show_ram', True)),
        'private_mode': bool(data.get('private_mode', False)),
    }


def load_public_home_prefs():
    try:
        with open(PUBLIC_HOME_PREFS_FILE, 'r', encoding='utf-8') as file_obj:
            return normalize_public_home_prefs(json.load(file_obj))
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return DEFAULT_PUBLIC_HOME_PREFS.copy()


def save_public_home_prefs(raw):
    prefs = normalize_public_home_prefs(raw)
    os.makedirs(app.instance_path, exist_ok=True)
    with open(PUBLIC_HOME_PREFS_FILE, 'w', encoding='utf-8') as file_obj:
        json.dump(prefs, file_obj, ensure_ascii=False, indent=2)
    return prefs


def run_command(command, timeout=10):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        return result.returncode, (result.stdout or result.stderr).strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)


def parse_docker_container_output(output):
    containers = []
    for line in (output or '').splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split('\t')]
        if len(parts) < 4:
            continue
        container_id, name, status, image = parts[:4]
        ports = parts[4] if len(parts) > 4 else ''
        state = 'running' if status.lower().startswith('up') else 'stopped'
        containers.append({
            'id': container_id,
            'name': name,
            'status': status,
            'state': state,
            'image': image,
            'ports': ports,
        })
    return containers


def list_docker_containers():
    code, output = run_command(['docker', 'ps', '-a', '--format', '{{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}'], timeout=15)
    if code != 0:
        return []
    return parse_docker_container_output(output)


def can_manage_site(user, site):
    return bool(user and site and (user.is_admin or site.user_id == user.id))


def safe_extract_zip(archive, destination):
    destination = os.path.realpath(destination)
    for member in archive.infolist():
        target = os.path.realpath(os.path.join(destination, member.filename))
        if os.path.commonpath([destination, target]) != destination:
            raise ValueError('Архів містить небезпечний шлях')
    archive.extractall(destination)


def safe_site_path(site_path, relative_path=''):
    root = os.path.realpath(site_path)
    target = os.path.realpath(os.path.join(root, relative_path or ''))
    if os.path.commonpath([root, target]) != root:
        abort(400, 'Недійсний шлях')
    return target


CONSOLE_COMMANDS = {
    'uptime': ['uptime'],
    'memory': ['free', '-h'],
    'disk': ['df', '-h', '/'],
    'processes': ['ps', '-eo', 'pid,user,%cpu,%mem,comm', '--sort=-%mem'],
    'services': ['systemctl', '--no-pager', '--plain', '--type=service', '--state=running'],
    'failed_services': ['systemctl', '--no-pager', '--plain', '--type=service', '--state=failed'],
    'listeners': ['ss', '-tulpen'],
    'docker_ps': ['docker', 'ps', '--format', 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'],
    'docker_restarting': ['docker', 'ps', '--filter', 'status=restarting', '--format', 'table {{.Names}}\t{{.Status}}'],
    'firewall': ['ufw', 'status', 'numbered'],
    'panel_logs': ['journalctl', '-u', 'myh-guru.service', '-n', '60', '--no-pager'],
    'tunnel_logs': ['journalctl', '-u', 'cloudflared.service', '-n', '60', '--no-pager'],
}

MANAGED_SERVICES = {
    'panel': 'myh-guru',
    'tunnel': 'cloudflared',
    'docker': 'docker',
    'ssh': 'ssh',
    'fail2ban': 'fail2ban',
}

LOG_SOURCES = {
    'panel': {'label': 'myh-guru service', 'command': ['journalctl', '-u', 'myh-guru.service', '-n', '200', '--no-pager']},
    'tunnel': {'label': 'cloudflared service', 'command': ['journalctl', '-u', 'cloudflared.service', '-n', '200', '--no-pager']},
    'system': {'label': 'system journal', 'command': ['journalctl', '-n', '200', '--no-pager']},
    'nginx': {'label': 'nginx service', 'command': ['journalctl', '-u', 'nginx.service', '-n', '200', '--no-pager']},
}


def get_cloudflare_token():
    token = os.environ.get('CLOUDFLARE_API_TOKEN', '').strip()
    if token:
        return token
    if os.path.isfile(CLOUDFLARE_TOKEN_FILE):
        try:
            with open(CLOUDFLARE_TOKEN_FILE, 'r', encoding='utf-8', errors='ignore') as handle:
                token = ''.join(handle.read().split())
                if token:
                    return token
        except OSError:
            return ''
    if os.path.isfile(CLOUDFLARE_LOCAL_TOKEN_FILE):
        try:
            with open(CLOUDFLARE_LOCAL_TOKEN_FILE, 'r', encoding='utf-8', errors='ignore') as handle:
                token = ''.join(handle.read().split())
                if token:
                    return token
        except OSError:
            return ''
    return ''


def get_cloudflare_zone_name():
    if os.path.isfile(CLOUDFLARE_LOCAL_ZONE_FILE):
        try:
            with open(CLOUDFLARE_LOCAL_ZONE_FILE, 'r', encoding='utf-8', errors='ignore') as handle:
                zone_name = handle.read().strip().rstrip('.')
                if zone_name:
                    return zone_name
        except OSError:
            pass
    return (os.environ.get('CLOUDFLARE_ZONE_NAME') or CLOUDFLARE_ZONE_NAME or 'myh.guru').strip()


def save_cloudflare_credentials(token_value='', zone_name=''):
    os.makedirs(app.instance_path, exist_ok=True)
    token_value = ''.join((token_value or '').split())
    zone_name = (zone_name or '').strip().rstrip('.')
    if token_value:
        with open(CLOUDFLARE_LOCAL_TOKEN_FILE, 'w', encoding='utf-8') as handle:
            handle.write(token_value + '\n')
        os.chmod(CLOUDFLARE_LOCAL_TOKEN_FILE, 0o600)
    if zone_name:
        with open(CLOUDFLARE_LOCAL_ZONE_FILE, 'w', encoding='utf-8') as handle:
            handle.write(zone_name + '\n')
        os.chmod(CLOUDFLARE_LOCAL_ZONE_FILE, 0o600)


def cloudflare_config_summary():
    token = get_cloudflare_token()
    zone_name = get_cloudflare_zone_name()
    return {
        'zone_name': zone_name,
        'has_token': bool(token),
        'masked_token': (token[:4] + '...' + token[-4:]) if len(token) >= 12 else ('set' if token else ''),
        'token_source': (
            'env' if os.environ.get('CLOUDFLARE_API_TOKEN', '').strip()
            else 'file' if os.path.isfile(CLOUDFLARE_TOKEN_FILE)
            else 'panel'
        ) if token else 'missing',
    }


def cloudflare_request(method, path, payload=None, timeout=12):
    token = get_cloudflare_token()
    if not token:
        return {'success': False, 'errors': ['Cloudflare API token is missing']}
    request_body = None
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }
    if payload is not None:
        request_body = json.dumps(payload).encode('utf-8')
    request_object = urllib.request.Request(
        f'https://api.cloudflare.com/client/v4{path}',
        data=request_body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request_object, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode('utf-8', errors='ignore')
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {'success': False, 'errors': [body or str(exc)]}
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {'success': False, 'errors': [str(exc)]}


def get_cloudflare_zone_id(zone_name=None):
    zone_name = (zone_name or get_cloudflare_zone_name()).strip()
    payload = cloudflare_request('GET', f'/zones?name={zone_name}&status=active')
    if not payload.get('success'):
        return '', payload
    result = payload.get('result') or []
    return (result[0].get('id', '') if result else ''), payload


def cloudflare_dns_records(zone_id, record_type=None):
    suffix = ''
    if record_type:
        suffix = f'&type={record_type}'
    payload = cloudflare_request('GET', f'/zones/{zone_id}/dns_records?per_page=200{suffix}')
    return payload.get('result') or [], payload


def cloudflare_zone_settings(zone_id):
    keys = [
        'ssl', 'min_tls_version', 'tls_1_3', 'always_use_https',
        'automatic_https_rewrites', 'opportunistic_encryption', 'browser_check',
        'security_level', 'challenge_ttl', 'http3', 'ciphers', 'early_hints'
    ]
    settings = {}
    for key in keys:
        settings[key] = cloudflare_request('GET', f'/zones/{zone_id}/settings/{key}')
    return settings


def cloudflare_setting_values(zone_id):
    raw_settings = cloudflare_zone_settings(zone_id)
    values = {}
    for key, payload in raw_settings.items():
        values[key] = (payload.get('result') or {}).get('value') if payload.get('success') else None
    return values, raw_settings


def cloudflare_ssl_summary(zone_id):
    payload = cloudflare_request('GET', f'/zones/{zone_id}/ssl/verification')
    return payload

TEXT_EXTENSIONS = {'.html', '.htm', '.css', '.js', '.json', '.txt', '.md', '.xml', '.svg', '.py', '.ini', '.yml', '.yaml'}


def log_action(action, detail=''):
    user = db.session.get(User, session.get('user_id')) if session.get('user_id') else None
    entry = AuditLog(
        user_id=user.id if user else None,
        username=user.username if user else 'anonymous',
        action=action,
        detail=str(detail)[:500],
        ip_address=(request.headers.get('CF-Connecting-IP') or request.remote_addr or '')[:64],
    )
    db.session.add(entry)
    db.session.commit()


def record_deployment_event(site_name, deploy_mode='zip', status='success', detail='', repo_url=None):
    event = DeploymentEvent(
        site_name=site_name,
        deploy_mode=deploy_mode,
        status=status,
        detail=str(detail)[:500],
        repo_url=repo_url,
    )
    db.session.add(event)
    db.session.commit()
    return event


def append_deploy_log(site_name, message):
    log_dir = os.path.join(app.instance_path, 'deploy_logs')
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f'{site_name}.log')
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(log_path, 'a', encoding='utf-8') as handle:
        handle.write(f'[{timestamp}] {message}\n')
    return log_path


def directory_size(path):
    total = 0
    for root, _, filenames in os.walk(path):
        for filename in filenames:
            try:
                total += os.path.getsize(os.path.join(root, filename))
            except OSError:
                continue
    return total


def user_usage_bytes(user):
    return sum(directory_size(os.path.join(app.config['UPLOAD_FOLDER'], site.folder_name)) for site in user.sites)


def generate_temporary_password(length=20):
    alphabet = string.ascii_letters + string.digits + '!@#$%^&*()-_'
    while True:
        candidate = ''.join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(char.islower() for char in candidate)
            and any(char.isupper() for char in candidate)
            and any(char.isdigit() for char in candidate)
            and any(char in '!@#$%^&*()-_' for char in candidate)
        ):
            return candidate


def remove_tree(path):
    if not os.path.exists(path):
        return
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files:
            os.remove(os.path.join(root, name))
        for name in dirs:
            os.rmdir(os.path.join(root, name))
    os.rmdir(path)


def backup_directory(site):
    path = os.path.join(app.instance_path, 'site_backups', str(site.id))
    os.makedirs(path, exist_ok=True)
    return path


def list_site_backups(site):
    path = backup_directory(site)
    backups = []
    for filename in sorted(os.listdir(path), reverse=True):
        if re.fullmatch(r'\d{8}-\d{6}\.zip', filename):
            full_path = os.path.join(path, filename)
            backups.append({'name': filename, 'size': os.path.getsize(full_path), 'created': datetime.fromtimestamp(os.path.getmtime(full_path))})
    return backups


def create_backup_archive(site):
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    destination = os.path.join(backup_directory(site), f'{timestamp}.zip')
    source = os.path.join(app.config['UPLOAD_FOLDER'], site.folder_name)
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for root, _, filenames in os.walk(source):
            for filename in filenames:
                full_path = os.path.join(root, filename)
                archive.write(full_path, os.path.relpath(full_path, source))
    return destination


def extract_zip_to_site(zip_path, site_path):
    with zipfile.ZipFile(zip_path, 'r') as archive:
        archive.extractall(site_path)


def deploy_from_git(repo_url, site_path):
    repo_dir = os.path.join(app.instance_path, 'deploy_tmp', secure_filename(os.path.basename(repo_url).split('.')[0]))
    if os.path.exists(repo_dir):
        remove_tree(repo_dir)
    subprocess.run(['git', 'clone', '--depth', '1', repo_url, repo_dir], check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for root, dirs, files in os.walk(repo_dir):
        for name in dirs:
            if name in {'.git', '.github', '.vscode'}:
                continue
        for filename in files:
            if filename.endswith('.gitignore'):
                continue
    if os.path.isdir(os.path.join(repo_dir, 'public')):
        source_dir = os.path.join(repo_dir, 'public')
    elif os.path.isdir(os.path.join(repo_dir, 'dist')):
        source_dir = os.path.join(repo_dir, 'dist')
    else:
        source_dir = repo_dir
    for root, _, filenames in os.walk(source_dir):
        for filename in filenames:
            src_path = os.path.join(root, filename)
            rel_path = os.path.relpath(src_path, source_dir)
            dst_path = os.path.join(site_path, rel_path)
            os.makedirs(os.path.dirname(dst_path), exist_ok=True)
            with open(src_path, 'rb') as src_handle, open(dst_path, 'wb') as dst_handle:
                dst_handle.write(src_handle.read())
    remove_tree(repo_dir)


def dashboard_overview():
    users = User.query.all()
    sites = Site.query.all()
    active_users = [item for item in users if not item.is_banned]
    active_sites = [item for item in sites if not item.is_banned and item.owner and not item.owner.is_banned]
    custom_domains = sum(1 for site in sites if site.custom_domain)
    total_usage = sum(user_usage_bytes(item) for item in users)
    pending_jobs = JobTask.query.filter(JobTask.status.in_(['pending', 'running'])).count()
    active_agents = AgentNode.query.filter_by(status='online').count()
    return {
        'users_total': len(users),
        'users_active': len(active_users),
        'sites_total': len(sites),
        'sites_active': len(active_sites),
        'custom_domains': custom_domains,
        'total_usage_gb': round(total_usage / (1024 ** 3), 2),
        'pending_jobs': pending_jobs,
        'active_agents': active_agents,
    }


def list_restarting_containers():
    code, output = run_command(['docker', 'ps', '--filter', 'status=restarting', '--format', '{{.Names}}'])
    if code != 0:
        return []
    return [line.strip() for line in output.splitlines() if line.strip()]


def list_failed_services():
    code, output = run_command(['systemctl', '--no-pager', '--plain', '--type=service', '--state=failed', '--no-legend'])
    if code != 0:
        return []
    return [line.split()[0] for line in output.splitlines() if line.strip()]


def send_notification(message, level='info'):
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
    if bot_token and chat_id:
        try:
            payload = urllib.parse.urlencode({'chat_id': chat_id, 'text': message}).encode('utf-8')
            request_object = urllib.request.Request(f'https://api.telegram.org/bot{bot_token}/sendMessage', data=payload, method='POST')
            urllib.request.urlopen(request_object, timeout=10)
        except Exception:
            pass
    smtp_host = os.environ.get('SMTP_HOST', '').strip()
    smtp_user = os.environ.get('SMTP_USER', '').strip()
    smtp_password = os.environ.get('SMTP_PASSWORD', '').strip()
    smtp_to = os.environ.get('SMTP_TO', '').strip()
    if smtp_host and smtp_user and smtp_password and smtp_to:
        try:
            with smtplib.SMTP(smtp_host, 587, timeout=10) as smtp:
                smtp.starttls()
                smtp.login(smtp_user, smtp_password)
                smtp.sendmail(smtp_user, [smtp_to], f'Subject: [{level.upper()}] Hosting panel alert\n\n{message}')
        except Exception:
            pass


def collect_health_alerts():
    alerts = []
    failed = list_failed_services()
    restarting = list_restarting_containers()
    if failed:
        alerts.append({'level': 'danger', 'title': 'Failed services', 'detail': ', '.join(failed[:6])})
    if restarting:
        alerts.append({'level': 'warning', 'title': 'Restarting containers', 'detail': ', '.join(restarting[:6])})
    if alerts:
        send_notification('Hosting panel detected: ' + '; '.join(item['title'] for item in alerts), level='warning')
    return alerts


JOB_WORKER_STARTED = False
JOB_WORKER_LOCK = threading.Lock()
JOB_WORKER_WAKEUP = threading.Event()

JOB_TYPE_LABELS = {
    'site.backup': 'Backup сайту',
    'site.restore': 'Restore сайту',
    'site.delete': 'Delete сайту',
    'service.restart': 'Restart service',
    'agent.refresh': 'Refresh agent',
}

def job_payload_dict(payload_json):
    try:
        return json.loads(payload_json or '{}')
    except json.JSONDecodeError:
        return {}


def create_job(job_type, target=None, payload=None, created_by='system'):
    job = JobTask(
        job_type=job_type,
        target=target,
        payload_json=json.dumps(payload or {}),
        status='pending',
        progress=0,
        message='queued',
        result_json='{}',
        created_by=created_by,
    )
    db.session.add(job)
    db.session.commit()
    JOB_WORKER_WAKEUP.set()
    return job


def set_job_state(job, *, status=None, progress=None, message=None, result=None, started=False, finished=False):
    if status is not None:
        job.status = status
    if progress is not None:
        job.progress = progress
    if message is not None:
        job.message = message[:255]
    if result is not None:
        job.result_json = json.dumps(result)
    if started:
        job.started_at = datetime.now()
    if finished:
        job.finished_at = datetime.now()
    job.updated_at = datetime.now()
    db.session.commit()


def clean_site_backup_retention(site, limit=10):
    backups = list_site_backups(site)
    for old in backups[limit:]:
        try:
            os.remove(os.path.join(backup_directory(site), old['name']))
        except OSError:
            continue


def perform_job(job):
    payload = job_payload_dict(job.payload_json)
    if job.job_type == 'site.backup':
        site = db.session.get(Site, payload.get('site_id'))
        if not site:
            raise ValueError('Сайт не знайдено')
        set_job_state(job, progress=10, message=f'Backup {site.name} готується')
        create_backup_archive(site)
        set_job_state(job, progress=80, message=f'Backup {site.name} створено')
        clean_site_backup_retention(site)
        return {'site': site.name, 'folder_name': site.folder_name, 'backups_kept': 10}

    if job.job_type == 'site.restore':
        site = db.session.get(Site, payload.get('site_id'))
        backup_name = payload.get('backup_name', '')
        if not site:
            raise ValueError('Сайт не знайдено')
        archive_path = get_backup_path(site, backup_name)
        site_path = safe_site_path(app.config['UPLOAD_FOLDER'], site.folder_name)
        set_job_state(job, progress=25, message=f'Restore {site.name} очищається')
        for root, directories, filenames in os.walk(site_path, topdown=False):
            for filename in filenames:
                os.remove(os.path.join(root, filename))
            for directory in directories:
                os.rmdir(os.path.join(root, directory))
        set_job_state(job, progress=55, message=f'Restore {site.name} розпаковується')
        with zipfile.ZipFile(archive_path, 'r') as archive:
            safe_extract_zip(archive, site_path)
        return {'site': site.name, 'backup_name': backup_name}

    if job.job_type == 'site.delete':
        site = db.session.get(Site, payload.get('site_id'))
        if not site:
            raise ValueError('Сайт не знайдено')
        site_path = os.path.join(app.config['UPLOAD_FOLDER'], site.folder_name)
        if os.path.exists(site_path):
            create_backup_archive(site)
            remove_tree(site_path)
        site_name = site.name
        db.session.delete(site)
        db.session.commit()
        return {'site': site_name, 'deleted': True}

    if job.job_type == 'service.restart':
        service_id = payload.get('service_id', '')
        service_name = MANAGED_SERVICES.get(service_id, service_id)
        if not service_name:
            raise ValueError('Сервіс не підтримується')
        code, output = run_command(['systemctl', 'restart', service_name], timeout=20)
        if code != 0:
            raise RuntimeError(output or 'restart failed')
        return {'service': service_name, 'output': output}

    if job.job_type == 'agent.refresh':
        agent = db.session.get(AgentNode, payload.get('agent_id'))
        if not agent:
            raise ValueError('Агент не знайдено')
        agent.last_seen_at = datetime.now()
        agent.status = 'online'
        db.session.commit()
        return {'agent': agent.name, 'status': 'online'}

    raise ValueError(f'Unsupported job type: {job.job_type}')


def job_worker_loop():
    with app.app_context():
        while True:
            try:
                job = JobTask.query.filter_by(status='pending').order_by(JobTask.id.asc()).first()
                if not job:
                    JOB_WORKER_WAKEUP.wait(timeout=2)
                    JOB_WORKER_WAKEUP.clear()
                    continue
                set_job_state(job, status='running', progress=1, message='processing', started=True)
                result = perform_job(job)
                set_job_state(job, status='succeeded', progress=100, message='completed', result=result, finished=True)
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                try:
                    failed_job = db.session.get(JobTask, job.id) if 'job' in locals() and job else None
                    if failed_job:
                        set_job_state(
                            failed_job,
                            status='failed',
                            message=str(exc),
                            result={'error': str(exc), 'traceback': traceback.format_exc(limit=12)},
                            finished=True,
                        )
                except Exception:
                    db.session.rollback()
                time.sleep(1)


def start_job_worker():
    global JOB_WORKER_STARTED
    if os.environ.get('HOSTING_PANEL_DISABLE_JOB_WORKER') == '1':
        return
    with JOB_WORKER_LOCK:
        if JOB_WORKER_STARTED:
            return
        thread = threading.Thread(target=job_worker_loop, name='myh-job-worker', daemon=True)
        thread.start()
        JOB_WORKER_STARTED = True


AGENT_SHARED_SECRET = os.environ.get('HOSTING_PANEL_AGENT_SECRET') or panel_secret


def agent_secret_valid(request_obj):
    supplied = request_obj.headers.get('X-Agent-Secret', '')
    return bool(AGENT_SHARED_SECRET and supplied and secrets.compare_digest(AGENT_SHARED_SECRET, supplied))


def upsert_agent(payload, remote_ip=''):
    name = (payload.get('name') or '').strip()
    if not name:
        raise ValueError('Agent name required')
    agent = AgentNode.query.filter_by(name=name).first()
    if not agent:
        agent = AgentNode(name=name)
        db.session.add(agent)
    agent.host_ip = (payload.get('host_ip') or remote_ip or '').strip()[:64]
    agent.platform = (payload.get('platform') or 'linux').strip()[:80]
    agent.role = (payload.get('role') or 'general').strip()[:80]
    agent.version = (payload.get('version') or '1.0.0').strip()[:40]
    capabilities = payload.get('capabilities') or []
    if isinstance(capabilities, str):
        try:
            capabilities = json.loads(capabilities)
        except json.JSONDecodeError:
            capabilities = [capabilities]
    agent.capabilities_json = json.dumps(capabilities)
    agent.status = 'online'
    agent.last_seen_at = datetime.now()
    db.session.commit()
    return agent


start_job_worker()


def api_catalog():
    catalog = []
    for rule in sorted(app.url_map.iter_rules(), key=lambda item: item.rule):
        if rule.endpoint == 'static':
            continue
        methods = sorted(method for method in rule.methods if method not in {'HEAD', 'OPTIONS'})
        if not rule.rule.startswith('/api/') and not rule.rule.startswith('/developer/'):
            continue
        catalog.append({'path': rule.rule, 'methods': methods, 'endpoint': rule.endpoint})
    return catalog


# Декоратор для перевірки прав розробника (Крок 2)
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        user = User.query.get(session['user_id'])
        if not user or not user.is_admin:
            return abort(403)
        return f(*args, **kwargs)
    return decorated_function

# Динамічний перехоплювач піддоменів (враховує блокування сайтів і користувачів)
@app.before_request
def handle_subdomain():
    host = request.host.split(':', 1)[0].lower().rstrip('.')
    parts = host.split('.')
    custom_site = Site.query.filter_by(custom_domain=host).first()
    if custom_site:
        if custom_site.is_banned or (custom_site.owner and custom_site.owner.is_banned):
            abort(403)
        site_path = os.path.join(app.config['UPLOAD_FOLDER'], custom_site.folder_name)
        return send_from_directory(site_path, request.path.lstrip('/') or 'index.html')

    if len(parts) > 2 and not host.startswith('192.') and not host.startswith('127.'):
        subdomain = parts[0]
        
        if subdomain not in ['www', 'panel', 'myh']:
            site = Site.query.filter((Site.name == subdomain) | (Site.folder_name.like(f"%_{subdomain}"))).first()
            if site:
                # СУВОРА ПЕРЕВІРКА БЛОКУВАННЯ: перевіряємо сайт і власника
                if site.is_banned or (site.owner and site.owner.is_banned):
                    return "<h1>403 Forbidden</h1><p>Цей сайт або обліковий запис власника заблоковано адміністратором.</p>", 403

                site_path = os.path.join(app.config['UPLOAD_FOLDER'], site.folder_name)
                path = request.path.lstrip('/')
                if not path:
                    path = 'index.html'
                if os.path.exists(os.path.join(site_path, path)):
                    return send_from_directory(site_path, path)
                else:
                    return abort(404)
            else:
                abort(404)

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('home.html')


@app.route('/favicon.ico')
def favicon():
    return send_from_directory(
        os.path.join(app.root_path, 'static'),
        'favicon.ico',
        mimetype='image/png',
        max_age=86400,
    )

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        client_key = request.headers.get('CF-Connecting-IP') or request.remote_addr or 'unknown'
        now = time.monotonic()
        username = request.form['username'].strip().lower()
        account_key = f'acct:{username}'
        ip_key = f'ip:{client_key}'
        account_attempts = login_attempts[account_key]
        ip_attempts = login_attempts[ip_key]
        for attempts in (account_attempts, ip_attempts):
            while attempts and now - attempts[0] > LOGIN_WINDOW_SECONDS:
                attempts.popleft()
        if len(ip_attempts) >= LOGIN_MAX_IP_ATTEMPTS or len(account_attempts) >= LOGIN_MAX_ACCOUNT_ATTEMPTS:
            return render_template('login.html', error='Забагато спроб. Спробуйте через 5 хвилин.'), 429
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password, password):
            # Перевірка на блокування аккаунта
            if user.is_banned:
                error = "Ваш аккаунт заблоковано адміністратором!"
            else:
                login_attempts.pop(ip_key, None)
                login_attempts.pop(account_key, None)
                session.clear()
                session.permanent = True
                session['user_id'] = user.id
                session['username'] = user.username
                session['role'] = 'developer' if user.is_admin else 'user'
                user.last_login_at = datetime.now()
                db.session.commit()
                log_action('login', 'Успішний вхід')
                return redirect(url_for('dashboard'))
        else:
            ip_attempts.append(now)
            account_attempts.append(now)
            time.sleep(min(len(ip_attempts), 5) * 0.2)
            error = "Невірний логін або пароль!"
    return render_template('login.html', error=error)

@app.route('/set-language/<lang>')
def set_language(lang):
    if lang not in SUPPORTED_LANGUAGES:
        lang = DEFAULT_LANGUAGE
    session['language'] = lang
    next_url = request.args.get('next') or url_for('dashboard' if 'user_id' in session else 'login')
    return redirect(next_url)


@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404


@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user = User.query.get(session['user_id'])
    if user.is_banned:
        session.clear()
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        subdomain = request.form.get('site_name', '').strip().lower()
        site_type = 'static'
        owner = user
        if user.is_admin and request.form.get('owner_id'):
            owner = db.session.get(User, request.form.get('owner_id', type=int))
            if not owner or owner.is_banned:
                flash(translate('site_owner_required'), 'error')
                return redirect(url_for('dashboard'))
        if re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', subdomain):
            if Site.query.filter_by(name=subdomain).first():
                flash(translate('subdomain_taken'), 'error')
                return redirect(url_for('dashboard'))
            folder_name = f"{owner.username}_{subdomain}"
            site_path = os.path.join(app.config['UPLOAD_FOLDER'], folder_name)
            os.makedirs(site_path, exist_ok=True)
            
            default_index = os.path.join(site_path, 'index.html')
            if not os.path.exists(default_index):
                with open(default_index, 'w', encoding='utf-8') as f:
                    f.write(f"<h1>{subdomain}.myh.guru працює</h1><p>Завантажте файли статичного сайту через файловий менеджер.</p>")

            new_site = Site(name=subdomain, folder_name=folder_name, php_version=site_type, user_id=owner.id)
            db.session.add(new_site)
            db.session.commit()
            log_action('site.create', f'{subdomain} → {owner.username}')
        else:
            flash(translate('invalid_site_name'), 'error')
            
        return redirect(url_for('dashboard'))
    
    if user.is_admin:
        all_sites = Site.query.order_by(Site.id.desc()).all()
        metrics = get_server_metrics()
        services = [
            {'name': 'Панель', 'key': 'myh-guru'},
            {'name': 'Cloudflare Tunnel', 'key': 'cloudflared'},
            {'name': 'SSH', 'key': 'ssh'},
            {'name': 'Docker', 'key': 'docker'},
        ]
        service_statuses = {item['key']: get_service_status(item['key']) for item in services}
        users = User.query.filter_by(is_banned=False).order_by(User.username).all()
        usage = {item.id: user_usage_bytes(item) for item in users}
        recent_logs = AuditLog.query.order_by(AuditLog.id.desc()).limit(15).all()
        overview = dashboard_overview()
        return render_template('developer_dashboard.html', user=user, sites=all_sites, users=users, usage=usage, recent_logs=recent_logs, metrics=metrics, services=service_statuses, overview=overview)

    user_sites = Site.query.filter_by(user_id=session['user_id']).all()
    usage_bytes_value = user_usage_bytes(user)
    summary = {
        'sites': len(user_sites),
        'used_mb': round(usage_bytes_value / 1048576, 1),
        'domains': sum(1 for site in user_sites if site.custom_domain),
        'backups': sum(len(list_site_backups(site)) for site in user_sites),
    }
    metrics = get_server_metrics()
    return render_template('dashboard.html', user=user, sites=user_sites, usage_bytes=usage_bytes_value, summary=summary, metrics=metrics)


@app.route('/developer/dashboard')
@admin_required
def developer_dashboard():
    return redirect(url_for('dashboard'))

# --- НОВІ МАРШРУТИ РЕЖИМУ РОЗРОБНИКА (Крок 2) ---

@app.route('/developer/users')
@admin_required
def developer_users():
    users = User.query.order_by(User.username).all()
    sites = Site.query.order_by(Site.id.desc()).all()
    usage = {user.id: user_usage_bytes(user) for user in users}
    logs = AuditLog.query.order_by(AuditLog.id.desc()).limit(100).all()
    return render_template('developer_users.html', users=users, usage=usage, logs=logs, sites=sites)


@app.route('/developer/users/create', methods=['POST'])
@admin_required
def developer_create_user():
    username = request.form.get('username', '').strip().lower()
    first_name = request.form.get('first_name', '').strip()
    last_name = request.form.get('last_name', '').strip()
    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')
    quota_mb = request.form.get('quota_mb', type=int) or 51200
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{2,31}', username):
        flash(translate('invalid_username'), 'error')
    elif not first_name or not last_name or '@' not in email:
        flash(translate('invalid_user_profile'), 'error')
    elif len(password) < 12:
        flash(translate('password_short'), 'error')
    elif not 64 <= quota_mb <= 51200:
        flash(translate('quota_invalid'), 'error')
    elif User.query.filter((User.username == username) | (User.email == email)).first():
        flash(translate('user_exists'), 'error')
    else:
        new_user = User(
            username=username,
            first_name=first_name,
            last_name=last_name,
            middle_name='',
            phone='—',
            email=email,
            password=generate_password_hash(password),
            is_admin=False,
            must_change_password=True,
            quota_mb=quota_mb,
        )
        db.session.add(new_user)
        db.session.commit()
        log_action('user.create', username)
        flash(f'{translate("user_created")} {username}.', 'success')
    return redirect(url_for('developer_users'))


@app.route('/developer/users/<int:user_id>/update', methods=['POST'])
@admin_required
def developer_update_user(user_id):
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    email = request.form.get('email', '').strip().lower()
    first_name = request.form.get('first_name', '').strip()
    last_name = request.form.get('last_name', '').strip()
    quota_mb = request.form.get('quota_mb', type=int) or user.quota_mb
    must_change_password = request.form.get('must_change_password') == '1'
    if not first_name or not last_name:
        flash(translate('invalid_user_profile'), 'error')
        return redirect(url_for('developer_users'))
    if '@' not in email:
        flash('Вкажіть коректний email.', 'error')
        return redirect(url_for('developer_users'))
    if not 64 <= quota_mb <= 51200:
        flash('Квота повинна бути від 64 MB до 51200 MB.', 'error')
        return redirect(url_for('developer_users'))
    duplicate = User.query.filter(User.email == email, User.id != user.id).first()
    if duplicate:
        flash('Цей email вже використовується іншим користувачем.', 'error')
        return redirect(url_for('developer_users'))
    user.first_name = first_name
    user.last_name = last_name
    user.email = email
    user.quota_mb = quota_mb
    user.must_change_password = must_change_password
    db.session.commit()
    log_action('user.update', f'{user.username}: quota={quota_mb} must_change={must_change_password}')
    flash(f'{translate("user_profile_updated")} {user.username}.', 'success')
    return redirect(url_for('developer_users'))


@app.route('/developer/users/<int:user_id>/reset-password', methods=['POST'])
@admin_required
def developer_reset_password(user_id):
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    temporary_password = generate_temporary_password()
    user.password = generate_password_hash(temporary_password)
    user.must_change_password = True
    db.session.commit()
    log_action('user.reset_password', user.username)
    flash(f'{translate("password_reset")} {user.username}: {temporary_password}', 'success')
    return redirect(url_for('developer_users'))


@app.route('/developer/users/<int:user_id>/toggle-admin', methods=['POST'])
@admin_required
def developer_toggle_admin(user_id):
    current_admin = db.session.get(User, session.get('user_id'))
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    if user.id == current_admin.id and user.is_admin:
        flash('Не можна зняти роль адміністратора із власного облікового запису.', 'error')
        return redirect(url_for('developer_users'))
    user.is_admin = not user.is_admin
    db.session.commit()
    log_action('user.toggle_admin', f'{user.username}: admin={user.is_admin}')
    flash(f'{translate("role_updated")} {user.username}.', 'success')
    return redirect(url_for('developer_users'))

@app.route('/developer/toggle-user/<int:user_id>', methods=['POST'])
@admin_required
def toggle_user(user_id):
    user = User.query.get_or_404(user_id)
    # Забороняємо блокувати розробника за іменем або якщо він адміністратор
    if user.username == 'developer' or user.is_admin:
        # Можна додати повідомлення або просто проігнорувати спробу блокування
        return redirect(url_for('developer_users'))
        
    user.is_banned = not user.is_banned
    db.session.commit()
    log_action('user.toggle', f'{user.username}: banned={user.is_banned}')
    return redirect(url_for('developer_users'))

@app.route('/developer/delete-user/<int:user_id>', methods=['POST'])
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.username == 'developer' or user.is_admin or user.id == session.get('user_id'):
        flash('Неможливо видалити цього користувача.', 'error')
        return redirect(url_for('developer_users'))

    for site in list(user.sites):
        site_path = os.path.join(app.config['UPLOAD_FOLDER'], site.folder_name)
        remove_tree(site_path)
        db.session.delete(site)

    username = user.username
    db.session.delete(user)
    db.session.commit()
    log_action('user.delete', username)
    flash(f'{translate("user_deleted")} {username}.', 'success')
    return redirect(url_for('developer_users'))

@app.route('/developer/toggle-site/<int:site_id>', methods=['POST'])
@admin_required
def toggle_site(site_id):
    site = Site.query.get_or_404(site_id)
    site.is_banned = not site.is_banned
    db.session.commit()
    log_action('site.toggle', f'{site.name}: banned={site.is_banned}')
    return redirect(url_for('developer_users'))


@app.route('/developer/sites/bulk', methods=['POST'])
@admin_required
def developer_bulk_sites():
    action = request.form.get('action', '').strip()
    site_ids = [site_id for site_id in request.form.getlist('site_ids') if site_id.isdigit()]
    if not site_ids:
        flash('Оберіть хоча б один сайт.', 'error')
        return redirect(url_for('developer_users'))
    sites = Site.query.filter(Site.id.in_([int(item) for item in site_ids])).all()
    if action not in {'ban', 'unban', 'delete'}:
        flash('Невідома bulk-операція.', 'error')
        return redirect(url_for('developer_users'))
    changed = 0
    for site in sites:
        if action == 'ban' and not site.is_banned:
            site.is_banned = True
            changed += 1
        elif action == 'unban' and site.is_banned:
            site.is_banned = False
            changed += 1
        elif action == 'delete':
            site_path = os.path.join(app.config['UPLOAD_FOLDER'], site.folder_name)
            if os.path.exists(site_path):
                create_backup_archive(site)
                remove_tree(site_path)
            db.session.delete(site)
            changed += 1
    db.session.commit()
    log_action('site.bulk', f'action={action} count={changed}')
    flash(f'Bulk-операція виконана: {action} ({changed}).', 'success')
    return redirect(url_for('developer_users'))


@app.route('/developer/site/<int:site_id>/backup', methods=['POST'])
@admin_required
def developer_site_backup(site_id):
    site = db.session.get(Site, site_id)
    if not site:
        abort(404)
    create_backup_archive(site)
    backups = list_site_backups(site)
    for old in backups[10:]:
        os.remove(os.path.join(backup_directory(site), old['name']))
    log_action('backup.create', f'{site.name} (developer)')
    flash(f'Резервну копію для {site.name} створено.', 'success')
    return redirect(url_for('dashboard'))


@app.route('/site/<int:site_id>/backup/queue', methods=['POST'])
def queue_site_backup(site_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    site = db.session.get(Site, site_id)
    if not can_manage_site(user, site):
        abort(403)
    job = create_job('site.backup', target=site.name, payload={'site_id': site.id}, created_by=user.username)
    log_action('backup.queue', f'#{job.id} {site.name}')
    flash(f'{translate("backup_queued")} #{job.id}.', 'success')
    return redirect(url_for('manage_site', folder_name=site.folder_name))


@app.route('/site/<int:site_id>/backup/<backup_name>/restore/queue', methods=['POST'])
def queue_restore_site_backup(site_id, backup_name):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    site = db.session.get(Site, site_id)
    if not can_manage_site(user, site):
        abort(403)
    get_backup_path(site, backup_name)
    job = create_job('site.restore', target=f'{site.name}:{backup_name}', payload={'site_id': site.id, 'backup_name': backup_name}, created_by=user.username)
    log_action('backup.restore.queue', f'#{job.id} {site.name}:{backup_name}')
    flash(f'{translate("restore_queued")} #{job.id}.', 'success')
    return redirect(url_for('manage_site', folder_name=site.folder_name))

# -----------------------------------------------

@app.route('/site/<folder_name>', methods=['GET', 'POST'])
def manage_site(folder_name):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user = User.query.get(session['user_id'])
    site = Site.query.filter_by(folder_name=folder_name).first()
    
    if not can_manage_site(user, site):
        return abort(403)
        
    site_path = os.path.join(app.config['UPLOAD_FOLDER'], folder_name)
    
    if request.method == 'POST':
        action = request.form.get('action', 'upload')
        if action == 'mkdir':
            folder = request.form.get('folder_name', '').strip().replace('\\', '/')
            if not folder or any(part in {'', '.', '..'} for part in folder.split('/')):
                flash(translate('invalid_directory_name'), 'error')
            else:
                os.makedirs(safe_site_path(site_path, folder), exist_ok=True)
                flash(translate('directory_created'), 'success')
        elif action == 'save':
            relative = request.form.get('file_path', '')
            target = safe_site_path(site_path, relative)
            content = request.form.get('content', '')
            if len(content.encode('utf-8')) > 1024 * 1024 or not os.path.isfile(target):
                abort(400)
            with open(target, 'w', encoding='utf-8', newline='') as handle:
                handle.write(content)
            flash(translate('file_saved'), 'success')
            log_action('file.edit', f'{site.name}/{relative}')
        elif action == 'rename':
            relative = request.form.get('file_path', '')
            new_name = secure_filename(request.form.get('new_name', ''))
            source = safe_site_path(site_path, relative)
            if not new_name or not os.path.exists(source):
                abort(400)
            destination = safe_site_path(site_path, os.path.join(os.path.dirname(relative), new_name))
            if os.path.exists(destination):
                flash(translate('file_exists'), 'error')
            else:
                os.rename(source, destination)
                log_action('file.rename', f'{site.name}/{relative} → {new_name}')
                flash('Перейменовано.', 'success')
        else:
            target_dir = safe_site_path(site_path, request.form.get('target_dir', ''))
            os.makedirs(target_dir, exist_ok=True)
            uploaded = 0
            quota_bytes = site.owner.quota_mb * 1024 * 1024
            for file in request.files.getlist('files'):
                if not file or not file.filename:
                    continue
                filename = secure_filename(os.path.basename(file.filename))
                if not filename:
                    continue
                file.stream.seek(0, os.SEEK_END)
                upload_size = file.stream.tell()
                file.stream.seek(0)
                if user_usage_bytes(site.owner) + upload_size > quota_bytes:
                    flash(f'{filename}: недостатньо доступної квоти.', 'error')
                    continue
                file_path = os.path.join(target_dir, filename)
                file.save(file_path)
                if filename.lower().endswith('.zip'):
                    try:
                        with zipfile.ZipFile(file_path, 'r') as zip_ref:
                            extracted_size = sum(item.file_size for item in zip_ref.infolist())
                            if len(zip_ref.infolist()) > 1000 or extracted_size > 128 * 1024 * 1024:
                                raise ValueError('Архів перевищує безпечний ліміт')
                            if user_usage_bytes(site.owner) - upload_size + extracted_size > quota_bytes:
                                raise ValueError('Розпакований архів перевищить квоту користувача')
                            safe_extract_zip(zip_ref, target_dir)
                    except (zipfile.BadZipFile, ValueError) as exc:
                        flash(str(exc), 'error')
                    finally:
                        os.remove(file_path)
                uploaded += 1
            if uploaded:
                log_action('file.upload', f'{site.name}: {uploaded} файлів')
                flash(f'{translate("files_uploaded")} {uploaded}.', 'success')
        return redirect(url_for('manage_site', folder_name=folder_name))
        
    files = []
    directories = []
    for root, dirs, filenames in os.walk(site_path):
        for directory in dirs:
            directories.append(os.path.relpath(os.path.join(root, directory), site_path))
        for f in filenames:
            rel_path = os.path.relpath(os.path.join(root, f), site_path)
            files.append({'path': rel_path, 'size': os.path.getsize(os.path.join(root, f))})

    edit_path = request.args.get('edit', '')
    edit_content = None
    if edit_path:
        target = safe_site_path(site_path, edit_path)
        if os.path.isfile(target) and os.path.getsize(target) <= 1024 * 1024 and os.path.splitext(target)[1].lower() in TEXT_EXTENSIONS:
            try:
                with open(target, 'r', encoding='utf-8') as handle:
                    edit_content = handle.read()
            except UnicodeDecodeError:
                flash('Цей файл не є текстовим.', 'error')

    return render_template('manage_site.html', site=site, files=sorted(files, key=lambda item: item['path']), directories=sorted(directories), edit_path=edit_path, edit_content=edit_content, backups=list_site_backups(site), usage_bytes=user_usage_bytes(site.owner))

@app.route('/view-site/<folder_name>/', defaults={'subpath': 'index.html'})
@app.route('/view-site/<folder_name>/<path:subpath>')
def view_site(folder_name, subpath):
    site = Site.query.filter_by(folder_name=folder_name).first()
    if not site or site.is_banned or (site.owner and site.owner.is_banned):
        return abort(404)
        
    site_path = os.path.join(app.config['UPLOAD_FOLDER'], folder_name)
    return send_from_directory(site_path, subpath)


@app.route('/site/<int:site_id>/backup', methods=['POST'])
def create_site_backup(site_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    site = db.session.get(Site, site_id)
    if not can_manage_site(user, site):
        abort(403)
    create_backup_archive(site)
    backups = list_site_backups(site)
    for old in backups[10:]:
        os.remove(os.path.join(backup_directory(site), old['name']))
    log_action('backup.create', site.name)
    flash(translate('backup_created'), 'success')
    return redirect(url_for('manage_site', folder_name=site.folder_name))


def get_backup_path(site, backup_name):
    if not re.fullmatch(r'\d{8}-\d{6}\.zip', backup_name):
        abort(400)
    path = safe_site_path(backup_directory(site), backup_name)
    if not os.path.isfile(path):
        abort(404)
    return path


@app.route('/site/<int:site_id>/backup/<backup_name>/download')
def download_site_backup(site_id, backup_name):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    site = db.session.get(Site, site_id)
    if not can_manage_site(user, site):
        abort(403)
    return send_file(get_backup_path(site, backup_name), as_attachment=True, download_name=f'{site.name}-{backup_name}')


@app.route('/site/<int:site_id>/backup/<backup_name>/restore', methods=['POST'])
def restore_site_backup(site_id, backup_name):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    site = db.session.get(Site, site_id)
    if not can_manage_site(user, site):
        abort(403)
    archive_path = get_backup_path(site, backup_name)
    site_path = safe_site_path(app.config['UPLOAD_FOLDER'], site.folder_name)
    for root, directories, filenames in os.walk(site_path, topdown=False):
        for filename in filenames:
            os.remove(os.path.join(root, filename))
        for directory in directories:
            os.rmdir(os.path.join(root, directory))
    with zipfile.ZipFile(archive_path, 'r') as archive:
        safe_extract_zip(archive, site_path)
    log_action('backup.restore', f'{site.name}: {backup_name}')
    flash(translate('restore_completed'), 'success')
    return redirect(url_for('manage_site', folder_name=site.folder_name))


@app.route('/site/<int:site_id>/domain', methods=['POST'])
def set_custom_domain(site_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    site = db.session.get(Site, site_id)
    if not can_manage_site(user, site):
        abort(403)
    domain = request.form.get('custom_domain', '').strip().lower().rstrip('.')
    if domain and not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', domain):
        flash(translate('invalid_domain'), 'error')
    elif domain and Site.query.filter(Site.custom_domain == domain, Site.id != site.id).first():
        flash(translate('domain_taken'), 'error')
    else:
        site.custom_domain = domain or None
        db.session.commit()
        log_action('domain.update', f'{site.name}: {domain or "removed"}')
        flash(translate('domain_saved'), 'success')
    return redirect(url_for('manage_site', folder_name=site.folder_name))

@app.route('/delete-site/<int:site_id>', methods=['POST'])
def delete_site(site_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user = User.query.get(session['user_id'])
    site = Site.query.get_or_404(site_id)
    
    if not user.is_admin and site.user_id != user.id:
        return abort(403)

    site_path = os.path.join(app.config['UPLOAD_FOLDER'], site.folder_name)
    if os.path.exists(site_path):
        create_backup_archive(site)
        remove_tree(site_path)
        
    site_name = site.name
    db.session.delete(site)
    db.session.commit()
    log_action('site.delete', f'{site_name}; backup retained')
    
    return redirect(url_for('dashboard'))


@app.route('/api/metrics')
def api_metrics():
    if 'user_id' not in session:
        return jsonify({'error': translate('api_unauthorized')}), 401
    user = db.session.get(User, session['user_id'])
    if not user or not user.is_admin:
        return jsonify({'error': translate('api_forbidden')}), 403
    return jsonify(get_server_metrics())


@app.route('/healthz')
def healthz():
    db_ok, detail = check_database_health()
    repaired = False
    if not db_ok:
        try:
            ensure_database_schema(force=True)
            repaired = True
            db_ok, detail = check_database_health()
        except Exception as exc:
            db_ok = False
            detail = str(exc)

    status_code = 200 if db_ok else 503
    return jsonify({
        'ok': db_ok,
        'db_ok': db_ok,
        'repaired': repaired,
        'detail': detail,
        'timestamp': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
    }), status_code


@app.route('/api/public-status')
def api_public_status():
    metrics = get_server_metrics()
    prefs = load_public_home_prefs()
    cpu_value = metrics.get('cpu')
    ram_value = metrics.get('ram_percent')
    if prefs['private_mode']:
        cpu_value = None
        ram_value = None
    if not prefs['show_cpu']:
        cpu_value = None
    if not prefs['show_ram']:
        ram_value = None
    return jsonify({
        'online': True,
        'timestamp': metrics.get('timestamp'),
        'cpu': cpu_value,
        'ram_percent': ram_value,
        'disk_percent': metrics.get('disk_percent'),
        'prefs': prefs,
    })


@app.route('/api/public-status/preferences', methods=['POST'])
def api_public_status_preferences():
    if 'user_id' not in session:
        return jsonify({'error': translate('api_unauthorized')}), 401
    user = db.session.get(User, session['user_id'])
    if not user or not user.is_admin:
        return jsonify({'error': translate('api_forbidden')}), 403
    supplied = request.headers.get('X-CSRF-Token', '')
    expected = session.get('_csrf_token', '')
    if not supplied or not expected or not secrets.compare_digest(supplied, expected):
        return jsonify({'error': 'invalid csrf token'}), 400
    payload = request.get_json(silent=True) or {}
    prefs = save_public_home_prefs(payload)
    return jsonify({'success': True, 'prefs': prefs})


@app.route('/api/openapi.json')
def api_openapi_json():
    return jsonify({
        'openapi': '3.0.3',
        'info': {
            'title': 'myh.guru Hosting Panel API',
            'version': '1.0.0',
            'description': 'Catalog of hosting panel API endpoints.'
        },
        'paths': {item['path']: {method.lower(): {'operationId': item['endpoint']} for method in item['methods']} for item in api_catalog()},
    })


@app.route('/api/sites/<int:site_id>/status')
def api_site_status(site_id):
    if 'user_id' not in session:
        return jsonify({'online': False}), 401
    user = db.session.get(User, session['user_id'])
    site = db.session.get(Site, site_id)
    if not can_manage_site(user, site):
        abort(403)
    url = f'https://{site.name}.myh.guru/'
    started = time.monotonic()
    try:
        request_object = urllib.request.Request(url, method='HEAD', headers={'User-Agent': 'myh-monitor/1.0'})
        with urllib.request.urlopen(request_object, timeout=5) as response:
            status_code = response.status
        return jsonify({'online': status_code < 500, 'status': status_code, 'latency_ms': round((time.monotonic() - started) * 1000)})
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        return jsonify({'online': False, 'status': None, 'latency_ms': round((time.monotonic() - started) * 1000), 'error': str(exc)[:120]})


@app.route('/developer/logs')
@admin_required
def developer_logs():
    source = request.args.get('source', 'panel')
    source_key = source if source in LOG_SOURCES else 'panel'
    log_config = LOG_SOURCES[source_key]
    lines = request.args.get('lines', default='200', type=int) or 200
    lines = max(50, min(lines, 500))
    code, output = run_command(log_config['command'][:], timeout=20)
    if code != 0:
        output = output or 'Log source unavailable.'
    log_lines = output.splitlines()
    if len(log_lines) > lines:
        log_lines = log_lines[-lines:]
    return render_template('developer_logs.html', source=source_key, source_label=log_config['label'], lines=lines, log_lines=log_lines, log_sources=LOG_SOURCES)


@app.route('/developer/api-gateway')
@admin_required
def developer_api_gateway():
    routes = api_catalog()
    summary = {
        'routes': len(routes),
        'admin_routes': sum(1 for item in routes if item['path'].startswith('/developer/')),
        'public_routes': sum(1 for item in routes if item['path'].startswith('/api/') and 'GET' in item['methods']),
    }
    return render_template('developer_api_gateway.html', routes=routes, summary=summary)


@app.route('/api/console', methods=['POST'])
@admin_required
def api_console():
    payload = request.get_json(silent=True) or {}
    command_name = payload.get('command', '')
    command = CONSOLE_COMMANDS.get(command_name)
    if not command:
        return jsonify({'success': False, 'output': translate('invalid_command')}), 400
    code, output = run_command(command, timeout=15)
    return jsonify({'success': code == 0, 'output': (output or translate('no_data'))[:16000]})


@app.route('/api/platform/overview')
@admin_required
def api_platform_overview():
    services = []
    for label, service_key in MANAGED_SERVICES.items():
        status = get_service_status(service_key)
        services.append({
            'id': label,
            'service': service_key,
            'active': status['active'],
            'detail': status['detail'][:300],
        })
    restarting = list_restarting_containers()
    failed = list_failed_services()
    alerts = collect_health_alerts()
    metrics = get_server_metrics()
    return jsonify({
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'services': services,
        'restarting_containers': restarting,
        'failed_services': failed,
        'alerts': alerts,
        'metrics': metrics,
        'summary': {
            'services_total': len(services),
            'services_active': sum(1 for item in services if item['active']),
            'restarting_containers': len(restarting),
            'failed_services': len(failed),
        }
    })


@app.route('/api/platform/service/<service_id>/<action>', methods=['POST'])
@admin_required
def api_manage_service(service_id, action):
    if action not in {'start', 'stop', 'restart'}:
        return jsonify({'success': False, 'error': 'Unsupported action.'}), 400
    service_name = MANAGED_SERVICES.get(service_id)
    if not service_name:
        return jsonify({'success': False, 'error': 'Сервіс не підтримується.'}), 404
    code, output = run_command(['systemctl', action, service_name], timeout=20)
    status = get_service_status(service_name)
    log_action(f'service.{action}', f'{service_name}: code={code}')
    return jsonify({
        'success': code == 0,
        'service': service_name,
        'action': action,
        'active': status['active'],
        'detail': status['detail'][:300],
        'output': output[:500],
    }), (200 if code == 0 else 500)


@app.route('/api/docker/containers')
@admin_required
def api_docker_containers():
    containers = list_docker_containers()
    return jsonify({'containers': containers})


@app.route('/api/docker/container/<container_name>/<action>', methods=['POST'])
@admin_required
def api_manage_container(container_name, action):
    if action not in {'start', 'stop', 'restart'}:
        return jsonify({'success': False, 'error': 'Unsupported action.'}), 400
    code, output = run_command(['docker', action, container_name], timeout=30)
    return jsonify({'success': code == 0, 'container': container_name, 'action': action, 'output': output[:1000]}), (200 if code == 0 else 500)


@app.route('/api/jobs')
@admin_required
def api_jobs():
    limit = request.args.get('limit', type=int) or 50
    jobs = JobTask.query.order_by(JobTask.id.desc()).limit(min(limit, 100)).all()
    return jsonify({
        'jobs': [
            {
                'id': job.id,
                'type': job.job_type,
                'target': job.target,
                'status': job.status,
                'progress': job.progress,
                'message': job.message,
                'created_by': job.created_by,
                'created_at': job.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                'updated_at': job.updated_at.strftime('%Y-%m-%d %H:%M:%S'),
                'result': job_payload_dict(job.result_json),
            }
            for job in jobs
        ],
        'summary': {
            'pending': JobTask.query.filter_by(status='pending').count(),
            'running': JobTask.query.filter_by(status='running').count(),
            'succeeded': JobTask.query.filter_by(status='succeeded').count(),
            'failed': JobTask.query.filter_by(status='failed').count(),
        }
    })


@app.route('/api/jobs/enqueue', methods=['POST'])
@admin_required
def api_enqueue_job():
    payload = request.get_json(silent=True) or {}
    job_type = (payload.get('job_type') or '').strip()
    target = (payload.get('target') or '').strip()
    data = payload.get('payload') or {}
    if job_type not in JOB_TYPE_LABELS:
        return jsonify({'success': False, 'error': 'Непідтримуваний тип job'}), 400
    job = create_job(job_type, target=target, payload=data, created_by=(session.get('username') or 'admin'))
    log_action('job.enqueue', f'{job_type}:{target or "-"}')
    return jsonify({'success': True, 'job_id': job.id, 'status': job.status, 'message': job.message})


@app.route('/api/jobs/<int:job_id>/cancel', methods=['POST'])
@admin_required
def api_cancel_job(job_id):
    job = db.session.get(JobTask, job_id)
    if not job:
        return jsonify({'success': False, 'error': 'Job не знайдено'}), 404
    if job.status not in {'pending', 'running'}:
        return jsonify({'success': False, 'error': 'Job вже завершено'}), 400
    if job.status == 'running':
        return jsonify({'success': False, 'error': 'Running job не можна зупинити безпечним чином'}), 409
    set_job_state(job, status='cancelled', progress=0, message='cancelled', finished=True)
    log_action('job.cancel', f'#{job_id}')
    return jsonify({'success': True, 'job_id': job.id, 'status': job.status})


@app.route('/api/agents/heartbeat', methods=['POST'])
def api_agent_heartbeat():
    if not agent_secret_valid(request):
        return jsonify({'success': False, 'error': 'unauthorized'}), 401
    payload = request.get_json(silent=True) or {}
    try:
        agent = upsert_agent(payload, remote_ip=(request.remote_addr or ''))
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    return jsonify({
        'success': True,
        'agent': {
            'id': agent.id,
            'name': agent.name,
            'status': agent.status,
            'platform': agent.platform,
            'role': agent.role,
        }
    })


@app.route('/developer/infrastructure')
@admin_required
def developer_infrastructure():
    agents = AgentNode.query.order_by(AgentNode.last_seen_at.desc().nullslast(), AgentNode.name).all()
    jobs = JobTask.query.order_by(JobTask.id.desc()).limit(25).all()
    overview = dashboard_overview()
    topology = {
        'sites': Site.query.count(),
        'users': User.query.count(),
        'agents': AgentNode.query.count(),
        'jobs': JobTask.query.count(),
        'backups': sum(len(list_site_backups(site)) for site in Site.query.all()),
    }
    return render_template(
        'developer_infrastructure.html',
        overview=overview,
        agents=agents,
        jobs=jobs,
        topology=topology,
        services=list(MANAGED_SERVICES.items()),
        job_type_labels=JOB_TYPE_LABELS,
    )


@app.route('/developer/docker')
@admin_required
def developer_docker():
    containers = list_docker_containers()
    return render_template('developer_docker.html', containers=containers)


@app.route('/developer/notifications', methods=['GET', 'POST'])
@admin_required
def developer_notifications():
    if request.method == 'POST':
        message = (request.form.get('message') or '').strip()
        if message:
            send_notification(message, level='info')
            flash('Notification sent.', 'success')
        else:
            flash('Message is required.', 'error')
        return redirect(url_for('developer_notifications'))
    return render_template('developer_notifications.html')


@app.route('/api/webhook/notify', methods=['POST'])
def api_webhook_notify():
    payload = request.get_json(silent=True) or {}
    message = payload.get('message') or payload.get('event') or 'Webhook notification'
    send_notification(str(message), level='info')
    return jsonify({'success': True, 'message': str(message)})


@app.route('/api/github/webhook', methods=['POST'])
def api_github_webhook():
    payload = request.get_json(silent=True) or {}
    repo = payload.get('repository', {}).get('full_name') or payload.get('repository', {}).get('name') or 'unknown'
    ref = payload.get('ref') or 'unknown'
    site_name = payload.get('site_name') or 'default'
    site = Site.query.filter_by(name=site_name).first() if site_name != 'default' else None
    signature = request.headers.get('X-Hub-Signature-256', '')
    expected_secret = site.webhook_secret if site and site.webhook_secret else os.environ.get('GITHUB_WEBHOOK_SECRET', '').strip()
    if expected_secret:
        expected_signature = 'sha256=' + hashlib.sha256((expected_secret).encode('utf-8')).hexdigest()
        if not signature or not secrets.compare_digest(signature, expected_signature):
            return jsonify({'success': False, 'error': 'invalid signature'}), 401
    if site and site.webhook_branch:
        branch_name = ref.split('/')[-1]
        if branch_name != site.webhook_branch:
            return jsonify({'success': False, 'error': 'branch mismatch'}), 403
    message = f'GitHub webhook received for {repo} at {ref}'
    append_deploy_log(site_name if site else 'default', message)
    send_notification(message, level='info')
    if site:
        try:
            deploy_from_git(payload.get('repository', {}).get('clone_url') or payload.get('repository', {}).get('html_url') or '', os.path.join(app.config['UPLOAD_FOLDER'], site.folder_name))
            record_deployment_event(site.name, deploy_mode='git', status='success', detail='GitHub webhook deployment completed', repo_url=payload.get('repository', {}).get('clone_url'))
            append_deploy_log(site.name, 'GitHub webhook deployment completed')
            log_action('deploy.github', f'{site.name}:{repo}')
        except Exception as exc:
            record_deployment_event(site.name, deploy_mode='git', status='failed', detail=str(exc)[:500], repo_url=payload.get('repository', {}).get('clone_url'))
            append_deploy_log(site.name, f'GitHub webhook deployment failed: {exc}')
            log_action('deploy.github.failed', f'{site.name}:{repo}')
            return jsonify({'success': False, 'error': str(exc)}), 500
    return jsonify({'success': True, 'message': message})


@app.route('/developer/deploy/history')
@admin_required
def developer_deploy_history():
    events = DeploymentEvent.query.order_by(DeploymentEvent.created_at.desc()).limit(50).all()
    return render_template('developer_deploy_history.html', events=events)


@app.route('/developer/deploy', methods=['GET', 'POST'])
@admin_required
def developer_deploy():
    if request.method == 'POST':
        if request.form.get('action') == 'save_webhook_config':
            site_name = (request.form.get('site_name') or '').strip()
            secret = (request.form.get('webhook_secret') or '').strip()
            branch = (request.form.get('webhook_branch') or '').strip()
            if not site_name:
                flash('Site name is required.', 'error')
                return redirect(url_for('developer_deploy'))
            site = Site.query.filter_by(name=site_name).first()
            if not site:
                flash('Target site was not found.', 'error')
                return redirect(url_for('developer_deploy'))
            site.webhook_secret = secret or None
            site.webhook_branch = branch or None
            db.session.commit()
            flash('Webhook settings saved.', 'success')
            return redirect(url_for('developer_deploy'))
        deploy_mode = request.form.get('deploy_mode', 'zip')
        target_name = (request.form.get('target_name') or '').strip()
        if not target_name:
            flash('Target site is required.', 'error')
            return redirect(url_for('developer_deploy'))
        site = Site.query.filter_by(name=target_name).first()
        if not site:
            flash('Target site was not found.', 'error')
            return redirect(url_for('developer_deploy'))
        site_path = os.path.join(app.config['UPLOAD_FOLDER'], site.folder_name)
        os.makedirs(site_path, exist_ok=True)
        if deploy_mode == 'git':
            repo_url = (request.form.get('repo_url') or '').strip()
            if not repo_url:
                flash('Git repository URL is required.', 'error')
                return redirect(url_for('developer_deploy'))
            try:
                deploy_from_git(repo_url, site_path)
            except subprocess.CalledProcessError as exc:
                flash(f'Git deploy failed: {exc.output[:500]}', 'error')
                return redirect(url_for('developer_deploy'))
            record_deployment_event(site.name, deploy_mode='git', status='success', detail='Git deployment completed', repo_url=repo_url)
            log_action('deploy.git', f'{site.name}:{repo_url}')
            flash('Git deployment completed.', 'success')
        else:
            uploaded = request.files.get('archive')
            if not uploaded or not uploaded.filename:
                flash('ZIP archive is required.', 'error')
                return redirect(url_for('developer_deploy'))
            temp_path = os.path.join(app.instance_path, 'deploy_tmp', secure_filename(uploaded.filename))
            os.makedirs(os.path.dirname(temp_path), exist_ok=True)
            uploaded.save(temp_path)
            try:
                extract_zip_to_site(temp_path, site_path)
            except zipfile.BadZipFile as exc:
                flash(f'Invalid archive: {exc}', 'error')
                return redirect(url_for('developer_deploy'))
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            record_deployment_event(site.name, deploy_mode='zip', status='success', detail=f'ZIP deployment completed: {uploaded.filename}', repo_url=None)
            log_action('deploy.zip', f'{site.name}:{uploaded.filename}')
            flash('ZIP deployment completed.', 'success')
        return redirect(url_for('developer_deploy'))
    return render_template('developer_deploy.html')


@app.route('/developer/marketplace', methods=['GET', 'POST'])
@admin_required
def developer_marketplace():
    if request.method == 'POST':
        action = request.form.get('action', '')
        if action == 'create_module':
            slug = secure_filename(request.form.get('slug', '').strip().lower().replace(' ', '-'))
            name = request.form.get('name', '').strip()
            category = request.form.get('category', 'custom').strip()
            description = request.form.get('description', '').strip()
            version = request.form.get('version', '1.0.0').strip()
            source_url = request.form.get('source_url', '').strip()
            if not slug or not name:
                flash(translate('module_required'), 'error')
            elif PluginModule.query.filter_by(slug=slug).first():
                flash(translate('module_exists'), 'error')
            else:
                db.session.add(PluginModule(
                    slug=slug,
                    name=name,
                    category=category,
                    description=description,
                    version=version,
                    source_url=source_url,
                    enabled=True,
                    built_in=False,
                ))
                db.session.commit()
                log_action('module.create', slug)
                flash(translate('module_created'), 'success')
            return redirect(url_for('developer_marketplace'))

    modules = PluginModule.query.order_by(PluginModule.built_in.desc(), PluginModule.category, PluginModule.name).all()
    summary = {
        'total': len(modules),
        'enabled': sum(1 for module in modules if module.enabled),
        'built_in': sum(1 for module in modules if module.built_in),
    }
    return render_template('developer_marketplace.html', modules=modules, summary=summary)


@app.route('/developer/modules/<int:module_id>/toggle', methods=['POST'])
@admin_required
def toggle_module(module_id):
    module = db.session.get(PluginModule, module_id)
    if not module:
        abort(404)
    module.enabled = not module.enabled
    db.session.commit()
    log_action('module.toggle', f'{module.slug}: enabled={module.enabled}')
    flash(f'Модуль {module.name} оновлено.', 'success')
    return redirect(url_for('developer_marketplace'))


@app.route('/developer/modules/<int:module_id>/delete', methods=['POST'])
@admin_required
def delete_module(module_id):
    module = db.session.get(PluginModule, module_id)
    if not module:
        abort(404)
    if module.built_in:
        flash('Built-in модулі видаляти не можна.', 'error')
        return redirect(url_for('developer_marketplace'))
    slug = module.slug
    db.session.delete(module)
    db.session.commit()
    log_action('module.delete', slug)
    flash(f'Модуль {slug} видалено.', 'success')
    return redirect(url_for('developer_marketplace'))


@app.route('/developer/backup-center')
@admin_required
def developer_backup_center():
    sites = Site.query.order_by(Site.name).all()
    backup_rows = []
    total_backups = 0
    total_size = 0
    sites_with_backups = 0
    for site in sites:
        backups = list_site_backups(site)
        if backups:
            sites_with_backups += 1
        total_backups += len(backups)
        total_size += sum(item['size'] for item in backups)
        backup_rows.append({
            'site': site,
            'backups': backups,
            'count': len(backups),
            'size': sum(item['size'] for item in backups),
            'latest': backups[0] if backups else None,
        })
    summary = {
        'total_backups': total_backups,
        'sites_with_backups': sites_with_backups,
        'total_size_gb': round(total_size / (1024 ** 3), 2),
        'pending_jobs': JobTask.query.filter_by(status='pending').count(),
    }
    recent_jobs = JobTask.query.filter(JobTask.job_type.in_(['site.backup', 'site.restore', 'site.delete'])).order_by(JobTask.id.desc()).limit(20).all()
    return render_template(
        'developer_backup_center.html',
        summary=summary,
        backup_rows=backup_rows,
        recent_jobs=recent_jobs,
    )


@app.route('/developer/dns-ssl', methods=['GET', 'POST'])
@admin_required
def developer_dns_ssl():
    config = cloudflare_config_summary()
    zone_name = config['zone_name']
    zone_id, zone_payload = get_cloudflare_zone_id(zone_name)
    records = []
    settings = {}
    raw_settings = {}
    ssl_summary = {}
    error_message = None

    if zone_id:
        records, _ = cloudflare_dns_records(zone_id)
        settings, raw_settings = cloudflare_setting_values(zone_id)
        ssl_summary = cloudflare_ssl_summary(zone_id)
    else:
        error_message = 'Не вдалося знайти активну Cloudflare-зону або токен недійсний.'
        details = zone_payload.get('errors') if isinstance(zone_payload, dict) else None
        if details:
            error_message = f"{error_message} Деталі: {details}"

    if request.method == 'POST':
        action = request.form.get('action', '')
        if action == 'save_cloudflare_credentials':
            token_value = request.form.get('api_token', '')
            custom_zone_name = request.form.get('zone_name', '')
            if not token_value.strip() and not custom_zone_name.strip():
                flash('Вкажіть токен або імʼя зони.', 'error')
                return redirect(url_for('developer_dns_ssl'))
            save_cloudflare_credentials(token_value=token_value, zone_name=custom_zone_name)
            log_action('cloudflare.credentials.save', f'zone={custom_zone_name.strip() or zone_name}')
            flash('Cloudflare дані збережено.', 'success')
            return redirect(url_for('developer_dns_ssl'))
        if not zone_id:
            flash(error_message or 'Cloudflare недоступний.', 'error')
            return redirect(url_for('developer_dns_ssl'))

        if action == 'create_record':
            record_type = request.form.get('record_type', 'A').upper().strip()
            name = request.form.get('name', '').strip().rstrip('.')
            content = request.form.get('content', '').strip()
            ttl = request.form.get('ttl', type=int) or 1
            priority = request.form.get('priority', type=int)
            proxied = request.form.get('proxied') == '1'
            allowed_types = {'A', 'AAAA', 'CNAME', 'TXT', 'MX', 'SRV', 'CAA'}
            if record_type not in allowed_types or not name or not content:
                flash('Заповніть усі поля DNS-запису коректно.', 'error')
            else:
                payload = {
                    'type': record_type,
                    'name': name,
                    'content': content,
                    'ttl': ttl,
                }
                if record_type in {'A', 'AAAA', 'CNAME'}:
                    payload['proxied'] = proxied
                if record_type in {'MX', 'SRV'} and priority is not None:
                    payload['priority'] = priority
                response = cloudflare_request('POST', f'/zones/{zone_id}/dns_records', payload)
                if response.get('success'):
                    log_action('dns.record.create', f'{record_type} {name} -> {content}')
                    flash('DNS-запис створено.', 'success')
                else:
                    flash(f"Помилка Cloudflare: {response.get('errors') or 'невідомо'}", 'error')
            return redirect(url_for('developer_dns_ssl'))

        if action == 'update_setting':
            setting_key = request.form.get('setting_key', '')
            setting_value = request.form.get('setting_value', '').strip()
            allowed_settings = {'ssl', 'min_tls_version', 'tls_1_3', 'always_use_https', 'automatic_https_rewrites', 'opportunistic_encryption', 'browser_check', 'security_level', 'challenge_ttl', 'http3', 'early_hints'}
            if setting_key not in allowed_settings:
                flash('Невідоме налаштування.', 'error')
            else:
                response = cloudflare_request('PATCH', f'/zones/{zone_id}/settings/{setting_key}', {'value': setting_value})
                if response.get('success'):
                    log_action('dns.setting.update', f'{setting_key}={setting_value}')
                    flash(f'Налаштування {setting_key} оновлено.', 'success')
                else:
                    flash(f"Помилка Cloudflare: {response.get('errors') or 'невідомо'}", 'error')
            return redirect(url_for('developer_dns_ssl'))

    records = sorted(records, key=lambda item: (item.get('name', ''), item.get('type', '')))
    return render_template(
        'developer_dns_ssl.html',
        zone_name=zone_name,
        zone_id=zone_id,
        records=records,
        settings=settings,
        raw_settings=raw_settings,
        ssl_summary=ssl_summary,
        zone_payload=zone_payload,
        error_message=error_message,
        cloudflare_config=config,
    )


@app.route('/developer/dns-ssl/record/<record_id>/delete', methods=['POST'])
@admin_required
def developer_delete_dns_record(record_id):
    zone_id, _ = get_cloudflare_zone_id()
    if not zone_id:
        flash('Cloudflare зона недоступна.', 'error')
        return redirect(url_for('developer_dns_ssl'))
    response = cloudflare_request('DELETE', f'/zones/{zone_id}/dns_records/{record_id}')
    if response.get('success'):
        log_action('dns.record.delete', record_id)
        flash('DNS-запис видалено.', 'success')
    else:
        flash(f"Помилка Cloudflare: {response.get('errors') or 'невідомо'}", 'error')
    return redirect(url_for('developer_dns_ssl'))


@app.route('/account/password', methods=['POST'])
def change_password():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    current = request.form.get('current_password', '')
    new_password = request.form.get('new_password', '')
    confirmation = request.form.get('confirm_password', '')
    if not check_password_hash(user.password, current):
        flash('Поточний пароль неправильний.', 'error')
    elif len(new_password) < 12:
        flash('Новий пароль повинен містити щонайменше 12 символів.', 'error')
    elif new_password != confirmation:
        flash('Підтвердження пароля не збігається.', 'error')
    else:
        user.password = generate_password_hash(new_password)
        user.must_change_password = False
        db.session.commit()
        log_action('account.password', 'Пароль змінено')
        flash('Пароль успішно змінено.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/delete-file/<folder_name>', methods=['POST'])
def delete_file(folder_name):
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    user = User.query.get(session['user_id'])
    site = Site.query.filter_by(folder_name=folder_name).first()
    
    if not can_manage_site(user, site):
        return abort(403)
        
    file_to_delete = request.form.get('file_path')
    if file_to_delete:
        site_path = os.path.realpath(os.path.join(app.config['UPLOAD_FOLDER'], site.folder_name))
        file_path = os.path.realpath(os.path.join(site_path, file_to_delete))
        
        if os.path.commonpath([site_path, file_path]) == site_path:
            if os.path.isfile(file_path):
                os.remove(file_path)
                log_action('file.delete', f'{site.name}/{file_to_delete}')
            elif os.path.isdir(file_path):
                try:
                    os.rmdir(file_path)
                    log_action('directory.delete', f'{site.name}/{file_to_delete}')
                except OSError:
                    flash('Каталог не порожній.', 'error')
            
    return redirect(url_for('manage_site', folder_name=folder_name))

@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('login'))

def main():
    parser = argparse.ArgumentParser(description='Hosting panel management CLI')
    parser.add_argument('--create-admin', action='store_true', help='Create or reset the developer admin account')
    parser.add_argument('--username', default='developer', help='Admin username to create/reset')
    parser.add_argument('--password', default=None, help='Admin password to set')
    parser.add_argument('--email', default=None, help='Admin email to set')
    parser.add_argument('--force', action='store_true', help='Force reset the admin password and role')
    parser.add_argument('--run-server', action='store_true', help='Run the web server')
    args = parser.parse_args()

    if args.create_admin:
        create_or_reset_admin_user(username=args.username, password=args.password, email=args.email, force=args.force)
        return
    if args.run_server:
        start_job_worker()
        app.run(host='127.0.0.1', port=5000, debug=False)
        return
    parser.print_help()


if __name__ == '__main__':
    main()
