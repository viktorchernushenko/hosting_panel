from flask import Flask, Response, render_template, request, redirect, url_for, session, send_from_directory, send_file, abort, flash, jsonify, g, has_request_context, after_this_request
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from werkzeug.exceptions import HTTPException
from functools import wraps
from sqlalchemy import inspect, text, func
from sqlalchemy.exc import OperationalError
import json
import psutil
import os
import zipfile
import gzip
import subprocess
import re
import secrets
import hashlib
import hmac
import time
import argparse
import grp
import pwd
import socket
import sqlite3
import ssl
import urllib.request
import urllib.error
import urllib.parse
import string
import threading
import traceback
import shutil
import tempfile
import pymysql
from notification_service import NotificationService
from notification_config import NotificationConfigError, NotificationConfigStore
from runtime_engine import RUNTIME_VERSIONS, SUPPORTED_RUNTIMES, compose_action, detect_file_names, detect_stack, healthcheck, infer_runtime_commands, prepare_custom_docker, prepare_runtime, prepare_wordpress_runtime, recommend_runtime, validate_runtime_commands
from runtime_registry import public_runtime, runtime_catalog
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

app = Flask(__name__)
panel_secret = os.environ.get('HOSTING_PANEL_SECRET')
if not panel_secret or len(panel_secret) < 32:
    raise RuntimeError('HOSTING_PANEL_SECRET must be set and at least 32 characters long')
app.config['SECRET_KEY'] = panel_secret
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'HOSTING_PANEL_DATABASE_URI',
    f"sqlite:///{os.path.join(app.root_path, 'instance', 'hosting.db')}",
)
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
APPLICATION_PORT_LOCK = threading.Lock()
login_attempts = defaultdict(deque)
webhook_attempts = defaultdict(deque)
notification_test_attempts = defaultdict(deque)
LOGIN_WINDOW_SECONDS = 300
LOGIN_MAX_IP_ATTEMPTS = 10
LOGIN_MAX_ACCOUNT_ATTEMPTS = 6
CLOUDFLARE_ZONE_NAME = os.environ.get('CLOUDFLARE_ZONE_NAME', 'myh.guru')
CLOUDFLARE_TOKEN_FILE = os.environ.get('CLOUDFLARE_TOKEN_FILE', '/tmp/.cf_token')
CLOUDFLARE_LOCAL_TOKEN_FILE = os.path.join(app.instance_path, 'cloudflare_api_token')
CLOUDFLARE_LOCAL_ZONE_FILE = os.path.join(app.instance_path, 'cloudflare_zone_name')
with open(os.path.join(app.root_path, 'VERSION'), encoding='ascii') as version_file:
    APP_VERSION = version_file.read().strip()
if not re.fullmatch(r'\d+\.\d+\.\d+', APP_VERSION):
    raise RuntimeError('VERSION must contain a semantic version (MAJOR.MINOR.PATCH)')
STATUS_MODEL = {
    'site': ['provisioning', 'running', 'unhealthy', 'stopped', 'failed', 'deleting'],
    'deployment': ['queued', 'building', 'deploying', 'running', 'failed'],
    'domain': ['pending', 'dns_error', 'verified', 'active'],
    'ssl': ['pending', 'secure', 'expiring', 'failed'],
    'backup': ['creating', 'ready', 'failed', 'restoring'],
    'database': ['provisioning', 'active', 'failed', 'deleting'],
}
MYSQL_HOST = os.environ.get('MYSQL_HOST', '172.23.0.1').strip()
MYSQL_PORT = int(os.environ.get('MYSQL_PORT', '3306'))
MYSQL_PROVISION_CNF = os.environ.get('MYSQL_PROVISION_CNF', '/etc/mysql/myh-provisioner.cnf')
MYSQL_BACKUP_ROOT = os.environ.get('MYSQL_BACKUP_ROOT', '/srv/backups/mysql')
MYSQL_SSL_CA = os.environ.get('MYSQL_SSL_CA', '/etc/mysql/myh-ca.pem')
MYSQL_HEALTH_CACHE = {'checked_at': 0.0, 'ok': False}
APP_STACKS_ROOT = os.path.join(app.instance_path, 'app_stacks')
RUNTIME_CONFIG_FILE = os.path.join(app.instance_path, 'runtime_config.json')
RUNTIME_HEALTH_FILE = os.path.join(app.instance_path, 'runtime_health.json')


def scoped_site_backup_root(site):
    """Keep retained backups isolated when SQLite reuses a deleted site id."""
    safe_folder = re.sub(r'[^a-zA-Z0-9_.-]+', '-', site.folder_name or site.name or 'site').strip('-')
    return os.path.join(app.instance_path, 'site_backups', f'{site.id}-{safe_folder}')
REQUIRED_CORE_TABLES = {
    'user',
    'site',
    'audit_log',
    'job_task',
    'agent_node',
    'plugin_module',
    'deployment_event',
    'application_access',
    'sftp_account',
    'upload_history',
    'sftp_audit_event',
    'integration',
    'environment_variable',
    'database_resource',
}
USER_PERMISSION_SET = {
    'site.create',
    'site.manage',
    'files.view',
    'files.upload',
    'files.download',
    'files.create',
    'files.rename',
    'files.delete',
    'files.extract',
    'sftp.access',
    'sftp.create',
    'domain.view',
    'domain.manage',
    'ssl.manage',
    'database.view',
    'database.create',
    'environment.view',
    'environment.manage',
    'integration.view',
    'integration.manage',
    'backup.view',
    'backup.create',
    'backup.download',
    'backup.restore',
    'site.view',
    'application.view',
    'application.restart',
    'git.connect',
    'deployment.request',
    'deployment.execute',
    'logs.view',
    'health.view',
    'wp.uploads.manage',
}
DEVELOPER_PERMISSION_SET = {
    'site.create',
    'site.manage',
    'files.view',
    'files.upload',
    'files.download',
    'files.create',
    'files.rename',
    'files.delete',
    'files.extract',
    'sftp.access',
    'application.view',
    'application.start',
    'application.stop',
    'application.restart',
    'docker.view',
    'docker.restart',
    'deployment.view',
    'deployment.execute',
    'deployment.rollback',
    'git.view',
    'git.fetch',
    'git.pull',
    'logs.view',
    'environment.view',
    'environment.manage',
    'backup.view',
    'backup.create',
    'backup.restore',
    'health.view',
    'wp.uploads.manage',
    'wp.themes.manage',
    'wp.plugins.manage',
}
ADMIN_PERMISSION_SET = USER_PERMISSION_SET | DEVELOPER_PERMISSION_SET
WORDPRESS_UPLOADS_PERMISSION = 'wp.uploads.manage'
WORDPRESS_THEMES_PERMISSION = 'wp.themes.manage'
WORDPRESS_PLUGINS_PERMISSION = 'wp.plugins.manage'
WORDPRESS_PERMISSION_SET = {
    WORDPRESS_UPLOADS_PERMISSION,
    WORDPRESS_THEMES_PERMISSION,
    WORDPRESS_PLUGINS_PERMISSION,
}
SFTP_PROVISION_QUEUE_FILE = os.path.join(app.instance_path, 'sftp_provision_queue.jsonl')
SFTP_PROVISION_RESULT_FILE = os.path.join(app.instance_path, 'sftp_provision_result.jsonl')
SFTP_PROVISION_WORKER = os.environ.get('HOSTING_PANEL_SFTP_PROVISION_WORKER', '/usr/local/sbin/myh-sftp-provision-worker.sh')
SFTP_PROVISION_SERVICE = os.environ.get('HOSTING_PANEL_SFTP_PROVISION_SERVICE', 'myh-sftp-provision.service')
SFTP_CONNECTION_MODE = os.environ.get('HOSTING_PANEL_SFTP_CONNECTION_MODE', 'cloudflare').strip().lower()
SFTP_TUNNEL_HOST = os.environ.get('HOSTING_PANEL_SFTP_TUNNEL_HOST', 'ssh.myh.guru').strip()
SFTP_LOCAL_PORT = int(os.environ.get('HOSTING_PANEL_SFTP_LOCAL_PORT', '2222'))
SFTP_PROVISION_LOCK = threading.Lock()
API_CSRF_EXEMPT_PATHS = {
    '/api/agents/heartbeat',
    '/api/webhook/notify',
    '/api/github/webhook',
}
NOTIFY_WEBHOOK_SECRET = os.environ.get('HOSTING_PANEL_NOTIFY_WEBHOOK_SECRET', '').strip()
WEBHOOK_RATE_WINDOW_SECONDS = 300
WEBHOOK_RATE_MAX_REQUESTS = 60
ARCHIVE_MAX_ENTRIES = int(os.environ.get('ARCHIVE_MAX_ENTRIES', '5000'))
ARCHIVE_MAX_UNCOMPRESSED_BYTES = int(os.environ.get('ARCHIVE_MAX_UNCOMPRESSED_BYTES', str(1024 * 1024 * 1024)))
ARCHIVE_MAX_COMPRESSION_RATIO = int(os.environ.get('ARCHIVE_MAX_COMPRESSION_RATIO', '200'))
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

SERVICE_CATALOG = [
    {
        'slug': 'static-site',
        'title_uk': 'Static Site',
        'title_en': 'Static Site',
        'summary_uk': 'Швидке створення статичного сайту зі стартовим шаблоном.',
        'summary_en': 'Fast static site provisioning with starter content scaffold.',
        'category': 'hosting',
        'status': 'working',
        'site_type': 'static',
        'permission': 'site.create',
    },
    {
        'slug': 'wordpress-site',
        'title_uk': 'WordPress',
        'title_en': 'WordPress',
        'summary_uk': 'Створення WordPress-стеку через майстер створення сайту.',
        'summary_en': 'Provision a WordPress stack through the site creation wizard.',
        'category': 'hosting',
        'status': 'working',
        'site_type': 'wordpress',
        'permission': 'site.create',
    },
    {
        'slug': 'node-app',
        'title_uk': 'Node App',
        'title_en': 'Node App',
        'summary_uk': 'Створення Node.js застосунку з базовим scaffold.',
        'summary_en': 'Create a Node.js application with baseline stack scaffolding.',
        'category': 'hosting',
        'status': 'working',
        'site_type': 'node',
        'permission': 'site.create',
    },
    {
        'slug': 'php-app',
        'title_uk': 'PHP App',
        'title_en': 'PHP App',
        'summary_uk': 'Створення PHP застосунку через уніфікований wizard.',
        'summary_en': 'Create a PHP application through the unified wizard flow.',
        'category': 'hosting',
        'status': 'working',
        'site_type': 'php',
        'permission': 'site.create',
    },
    {
        'slug': 'python-app',
        'title_uk': 'Python App',
        'title_en': 'Python App',
        'summary_uk': 'Створення Python застосунку з типовими налаштуваннями.',
        'summary_en': 'Provision a Python application with standard platform defaults.',
        'category': 'hosting',
        'status': 'working',
        'site_type': 'python',
        'permission': 'site.create',
    },
    {
        'slug': 'docker-app', 'title_uk': 'Docker App', 'title_en': 'Docker App',
        'summary_uk': 'Dockerfile або policy-validated Docker Compose з керованим портом.',
        'summary_en': 'Dockerfile or policy-validated Docker Compose with a managed port.',
        'category': 'hosting', 'status': 'working', 'site_type': 'docker', 'permission': 'site.create',
    },
    {
        'slug': 'database',
        'title_uk': 'Database',
        'title_en': 'Database',
        'summary_uk': 'Керування БД через developer applications сторінку.',
        'summary_en': 'Manage database resources through the developer applications view.',
        'category': 'data',
        'status': 'working',
        'site_type': None,
        'permission': 'database.create',
    },
    {
        'slug': 'connect-git',
        'title_uk': 'Git / GitHub',
        'title_en': 'Git / GitHub',
        'summary_uk': 'Git deploy, webhook-конфіг і події розгортання для сайтів.',
        'summary_en': 'Git deployment, webhook config, and deployment event tracking.',
        'category': 'delivery',
        'status': 'working',
        'site_type': None,
        'permission': 'git.connect',
    },
    {
        'slug': 'connect-domain',
        'title_uk': 'Domain & DNS',
        'title_en': 'Domain & DNS',
        'summary_uk': 'Cloudflare DNS/SSL керування для платформи і доменів.',
        'summary_en': 'Cloudflare DNS and SSL management for platform domains.',
        'category': 'network',
        'status': 'working',
        'site_type': None,
        'permission': 'domain.manage',
    },
    {
        'slug': 'create-sftp',
        'title_uk': 'SFTP Access',
        'title_en': 'SFTP Access',
        'summary_uk': 'Профілі SFTP та доступ до призначених застосунків.',
        'summary_en': 'SFTP profiles and access scope for assigned applications.',
        'category': 'access',
        'status': 'working',
        'site_type': None,
        'permission': 'sftp.create',
    },
    {
        'slug': 'connect-smtp',
        'title_uk': 'SMTP Alerts',
        'title_en': 'SMTP Alerts',
        'summary_uk': 'Відправка SMTP/Telegram сповіщень із панелі.',
        'summary_en': 'Send SMTP/Telegram notifications from panel workflows.',
        'category': 'integrations',
        'status': 'partial',
        'site_type': None,
        'permission': 'integration.manage',
    },
    {
        'slug': 'connect-telegram',
        'title_uk': 'Telegram Alerts',
        'title_en': 'Telegram Alerts',
        'summary_uk': 'Інтеграція Telegram для оперативних алертів.',
        'summary_en': 'Telegram integration for operational and deploy alerts.',
        'category': 'integrations',
        'status': 'partial',
        'site_type': None,
        'permission': 'integration.manage',
    },
    {
        'slug': 'create-webhook',
        'title_uk': 'Webhook Triggers',
        'title_en': 'Webhook Triggers',
        'summary_uk': 'Захищені webhook endpoint-и для автоматизації подій.',
        'summary_en': 'Secure webhook endpoints for deployment and notification events.',
        'category': 'integrations',
        'status': 'working',
        'site_type': None,
        'permission': 'integration.manage',
    },
    {
        'slug': 'configure-backups',
        'title_uk': 'Backups & Restore',
        'title_en': 'Backups & Restore',
        'summary_uk': 'Керування backup/restore, квотами і чергою робіт.',
        'summary_en': 'Manage backup and restore operations, quotas, and job queues.',
        'category': 'operations',
        'status': 'working',
        'site_type': None,
        'permission': 'backup.create',
    },
    {
        'slug': 'php-health',
        'title_uk': 'PHP Health',
        'title_en': 'PHP Health',
        'summary_uk': 'Перевірка PHP runtime, bootstrap і metadata для PHP-сайтів.',
        'summary_en': 'Validate PHP runtime, bootstrap, and metadata for PHP sites.',
        'category': 'operations',
        'status': 'working',
        'site_type': None,
        'permission': 'health.view',
    },
]

SERVICE_CATALOG_CATEGORIES = {
    'hosting': {'uk': 'Хостинг', 'en': 'Hosting'},
    'delivery': {'uk': 'Доставка коду', 'en': 'Code Delivery'},
    'data': {'uk': 'Дані', 'en': 'Data'},
    'network': {'uk': 'Мережа', 'en': 'Network'},
    'access': {'uk': 'Доступ', 'en': 'Access'},
    'integrations': {'uk': 'Інтеграції', 'en': 'Integrations'},
    'operations': {'uk': 'Операції', 'en': 'Operations'},
}

SERVICE_CATALOG_STATUS = {
    'working': {'uk': 'Доступно', 'en': 'Available'},
    'partial': {'uk': 'Обмежено', 'en': 'Limited'},
    'broken': {'uk': 'Недоступно', 'en': 'Unavailable'},
    'missing': {'uk': 'У розробці', 'en': 'In development'},
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
    role = db.Column(db.String(20), nullable=False, default='user')
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
    runtime_type = db.Column(db.String(20), nullable=False, default='static')
    application_type = db.Column(db.String(40), nullable=False, default='custom')
    installation_mode = db.Column(db.String(20), nullable=False, default='automatic')
    provisioning_phase = db.Column(db.String(40), nullable=False, default='ready')
    runtime_version = db.Column(db.String(40), nullable=False, default='nginx-alpine')
    runtime_status = db.Column(db.String(20), nullable=False, default='configured')
    deployment_status = db.Column(db.String(20), nullable=False, default='pending')
    internal_port = db.Column(db.Integer, nullable=True)
    last_restart_at = db.Column(db.DateTime, nullable=True)
    install_command = db.Column(db.String(500), nullable=False, default='')
    build_command = db.Column(db.String(500), nullable=False, default='')
    start_command = db.Column(db.String(500), nullable=False, default='')
    spa_enabled = db.Column(db.Boolean, nullable=False, default=False)


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


class Integration(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey('site.id'), nullable=False)
    integration_type = db.Column(db.String(40), nullable=False)
    provider = db.Column(db.String(40), nullable=False, default='generic')
    status = db.Column(db.String(20), nullable=False, default='pending')
    config_json = db.Column(db.Text, nullable=False, default='{}')
    secret_ref = db.Column(db.String(500), nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    last_test_at = db.Column(db.DateTime, nullable=True)
    last_test_status = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)
    application = db.relationship('Site', backref='integrations', lazy=True)


class EnvironmentVariable(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey('site.id'), nullable=False)
    key = db.Column(db.String(120), nullable=False)
    secret_ref = db.Column(db.String(500), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)
    application = db.relationship('Site', backref='environment_variables', lazy=True)
    __table_args__ = (db.UniqueConstraint('application_id', 'key', name='uq_application_environment_key'),)


class DatabaseResource(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey('site.id'), nullable=False)
    display_name = db.Column(db.String(80), nullable=False, default='database')
    engine = db.Column(db.String(20), nullable=False, default='mysql')
    database_name = db.Column(db.String(64), nullable=False, unique=True)
    database_user = db.Column(db.String(64), nullable=False, unique=True)
    host = db.Column(db.String(120), nullable=False, default='172.23.0.1')
    port = db.Column(db.Integer, nullable=False, default=3306)
    secret_ref = db.Column(db.String(500), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='ready')
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)
    application = db.relationship('Site', backref='database_resources', lazy=True)


class ApplicationAccess(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey('site.id'), nullable=False, unique=True)
    owner_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    assigned_users_json = db.Column(db.Text, nullable=False, default='[]')
    assigned_developers_json = db.Column(db.Text, nullable=False, default='[]')
    permissions_user_json = db.Column(db.Text, nullable=False, default='[]')
    permissions_developer_json = db.Column(db.Text, nullable=False, default='[]')
    wordpress_permissions_user_json = db.Column(db.Text, nullable=False, default='[]')
    wordpress_permissions_developer_json = db.Column(db.Text, nullable=False, default='[]')
    file_root = db.Column(db.String(500), nullable=False, default='')
    upload_root = db.Column(db.String(500), nullable=False, default='')
    deployment_root = db.Column(db.String(500), nullable=False, default='')
    backup_root = db.Column(db.String(500), nullable=False, default='')
    max_file_size_mb = db.Column(db.Integer, nullable=False, default=32)
    max_upload_size_mb = db.Column(db.Integer, nullable=False, default=256)
    storage_quota_mb = db.Column(db.Integer, nullable=False, default=51200)
    backup_quota_mb = db.Column(db.Integer, nullable=False, default=10240)
    application_quota_mb = db.Column(db.Integer, nullable=False, default=51200)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)
    site = db.relationship('Site', backref=db.backref('application_access', uselist=False, lazy=True), lazy=True)


class SftpAccount(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    role = db.Column(db.String(20), nullable=False, default='user')
    assigned_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    assigned_applications_json = db.Column(db.Text, nullable=False, default='[]')
    chroot_directory = db.Column(db.String(500), nullable=False, default='')
    auth_type = db.Column(db.String(20), nullable=False, default='password')
    password_hash = db.Column(db.String(255), nullable=True)
    public_keys_json = db.Column(db.Text, nullable=False, default='[]')
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    last_login_at = db.Column(db.DateTime, nullable=True)
    system_state = db.Column(db.String(20), nullable=False, default='pending')
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)
    assigned_user = db.relationship('User', backref='sftp_accounts', lazy=True)


class UploadHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey('site.id'), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    username = db.Column(db.String(80), nullable=False, default='system')
    role = db.Column(db.String(20), nullable=False, default='user')
    filename = db.Column(db.String(255), nullable=False, default='')
    size_bytes = db.Column(db.Integer, nullable=False, default=0)
    source = db.Column(db.String(20), nullable=False, default='panel')
    status = db.Column(db.String(20), nullable=False, default='success')
    deployment = db.Column(db.String(120), nullable=False, default='')
    detail = db.Column(db.String(500), nullable=False, default='')
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    application = db.relationship('Site', backref='upload_history', lazy=True)
    user = db.relationship('User', backref='upload_history', lazy=True)


class SftpAuditEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sftp_account_id = db.Column(db.Integer, db.ForeignKey('sftp_account.id'), nullable=True)
    username = db.Column(db.String(80), nullable=False, default='')
    action = db.Column(db.String(60), nullable=False, default='')
    status = db.Column(db.String(20), nullable=False, default='success')
    detail = db.Column(db.String(500), nullable=False, default='')
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    sftp_account = db.relationship('SftpAccount', backref='audit_events', lazy=True)


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
            ('user', 'role', "ALTER TABLE user ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'user'", user_columns),
            ('site', 'php_version', "ALTER TABLE site ADD COLUMN php_version VARCHAR(20) NOT NULL DEFAULT '8.2'", site_columns),
            ('site', 'custom_domain', "ALTER TABLE site ADD COLUMN custom_domain VARCHAR(255)", site_columns),
            ('site', 'webhook_secret', "ALTER TABLE site ADD COLUMN webhook_secret VARCHAR(255)", site_columns),
            ('site', 'webhook_branch', "ALTER TABLE site ADD COLUMN webhook_branch VARCHAR(120)", site_columns),
            ('site', 'created_at', "ALTER TABLE site ADD COLUMN created_at DATETIME", site_columns),
            ('site', 'runtime_type', "ALTER TABLE site ADD COLUMN runtime_type VARCHAR(20) NOT NULL DEFAULT 'static'", site_columns),
            ('site', 'runtime_version', "ALTER TABLE site ADD COLUMN runtime_version VARCHAR(40) NOT NULL DEFAULT 'nginx-alpine'", site_columns),
            ('site', 'runtime_status', "ALTER TABLE site ADD COLUMN runtime_status VARCHAR(20) NOT NULL DEFAULT 'configured'", site_columns),
            ('site', 'deployment_status', "ALTER TABLE site ADD COLUMN deployment_status VARCHAR(20) NOT NULL DEFAULT 'pending'", site_columns),
            ('site', 'internal_port', "ALTER TABLE site ADD COLUMN internal_port INTEGER", site_columns),
            ('site', 'last_restart_at', "ALTER TABLE site ADD COLUMN last_restart_at DATETIME", site_columns),
            ('site', 'install_command', "ALTER TABLE site ADD COLUMN install_command VARCHAR(500) NOT NULL DEFAULT ''", site_columns),
            ('site', 'build_command', "ALTER TABLE site ADD COLUMN build_command VARCHAR(500) NOT NULL DEFAULT ''", site_columns),
            ('site', 'start_command', "ALTER TABLE site ADD COLUMN start_command VARCHAR(500) NOT NULL DEFAULT ''", site_columns),
            ('site', 'spa_enabled', "ALTER TABLE site ADD COLUMN spa_enabled BOOLEAN NOT NULL DEFAULT 0", site_columns),
        ]

        with db.engine.begin() as conn:
            for table_name, column_name, statement, existing in migrations:
                if table_name in table_names and column_name not in existing:
                    conn.execute(text(statement))
            if 'user' in table_names and 'quota_mb' in user_columns:
                conn.execute(text("UPDATE user SET quota_mb = 51200 WHERE quota_mb = 256"))
            conn.execute(text("UPDATE user SET role = 'admin' WHERE is_admin = 1 AND (role IS NULL OR role = '' OR role = 'user')"))
            conn.execute(text("UPDATE user SET role = 'user' WHERE role IS NULL OR role = ''"))
            if 'site' in table_names:
                conn.execute(text("UPDATE site SET runtime_type = 'static' WHERE runtime_type IS NULL OR runtime_type = ''"))
                conn.execute(text("UPDATE site SET runtime_version = 'nginx-alpine' WHERE runtime_version IS NULL OR runtime_version = ''"))

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
            if 'application_access' not in job_tables:
                conn.execute(text("""
                    CREATE TABLE application_access (
                        id INTEGER PRIMARY KEY,
                        site_id INTEGER NOT NULL UNIQUE,
                        owner_user_id INTEGER NOT NULL,
                        assigned_users_json TEXT NOT NULL DEFAULT '[]',
                        assigned_developers_json TEXT NOT NULL DEFAULT '[]',
                        permissions_user_json TEXT NOT NULL DEFAULT '[]',
                        permissions_developer_json TEXT NOT NULL DEFAULT '[]',
                        wordpress_permissions_user_json TEXT NOT NULL DEFAULT '[]',
                        wordpress_permissions_developer_json TEXT NOT NULL DEFAULT '[]',
                        file_root VARCHAR(500) NOT NULL DEFAULT '',
                        upload_root VARCHAR(500) NOT NULL DEFAULT '',
                        deployment_root VARCHAR(500) NOT NULL DEFAULT '',
                        backup_root VARCHAR(500) NOT NULL DEFAULT '',
                        max_file_size_mb INTEGER NOT NULL DEFAULT 32,
                        max_upload_size_mb INTEGER NOT NULL DEFAULT 256,
                        storage_quota_mb INTEGER NOT NULL DEFAULT 51200,
                        backup_quota_mb INTEGER NOT NULL DEFAULT 10240,
                        application_quota_mb INTEGER NOT NULL DEFAULT 51200,
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL
                    )
                """))
            if 'sftp_account' not in job_tables:
                conn.execute(text("""
                    CREATE TABLE sftp_account (
                        id INTEGER PRIMARY KEY,
                        username VARCHAR(80) UNIQUE NOT NULL,
                        role VARCHAR(20) NOT NULL DEFAULT 'user',
                        assigned_user_id INTEGER,
                        assigned_applications_json TEXT NOT NULL DEFAULT '[]',
                        chroot_directory VARCHAR(500) NOT NULL DEFAULT '',
                        auth_type VARCHAR(20) NOT NULL DEFAULT 'password',
                        password_hash VARCHAR(255),
                        public_keys_json TEXT NOT NULL DEFAULT '[]',
                        enabled BOOLEAN NOT NULL DEFAULT 1,
                        last_login_at DATETIME,
                        system_state VARCHAR(20) NOT NULL DEFAULT 'pending',
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL
                    )
                """))
            if 'upload_history' not in job_tables:
                conn.execute(text("""
                    CREATE TABLE upload_history (
                        id INTEGER PRIMARY KEY,
                        application_id INTEGER,
                        user_id INTEGER,
                        username VARCHAR(80) NOT NULL DEFAULT 'system',
                        role VARCHAR(20) NOT NULL DEFAULT 'user',
                        filename VARCHAR(255) NOT NULL DEFAULT '',
                        size_bytes INTEGER NOT NULL DEFAULT 0,
                        source VARCHAR(20) NOT NULL DEFAULT 'panel',
                        status VARCHAR(20) NOT NULL DEFAULT 'success',
                        deployment VARCHAR(120) NOT NULL DEFAULT '',
                        detail VARCHAR(500) NOT NULL DEFAULT '',
                        created_at DATETIME NOT NULL
                    )
                """))
            if 'sftp_audit_event' not in job_tables:
                conn.execute(text("""
                    CREATE TABLE sftp_audit_event (
                        id INTEGER PRIMARY KEY,
                        sftp_account_id INTEGER,
                        username VARCHAR(80) NOT NULL DEFAULT '',
                        action VARCHAR(60) NOT NULL DEFAULT '',
                        status VARCHAR(20) NOT NULL DEFAULT 'success',
                        detail VARCHAR(500) NOT NULL DEFAULT '',
                        created_at DATETIME NOT NULL
                    )
                """))
            if 'integration' not in job_tables:
                conn.execute(text("""
                    CREATE TABLE integration (
                        id INTEGER PRIMARY KEY,
                        application_id INTEGER NOT NULL,
                        integration_type VARCHAR(40) NOT NULL,
                        provider VARCHAR(40) NOT NULL DEFAULT 'generic',
                        status VARCHAR(20) NOT NULL DEFAULT 'pending',
                        config_json TEXT NOT NULL DEFAULT '{}',
                        secret_ref VARCHAR(500),
                        created_by INTEGER NOT NULL,
                        last_test_at DATETIME,
                        last_test_status VARCHAR(20),
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL
                    )
                """))

        inspector = inspect(db.engine)
        if 'site' in inspector.get_table_names():
            site_columns = {column['name'] for column in inspector.get_columns('site')}
            if 'application_type' not in site_columns:
                with db.engine.begin() as conn:
                    conn.execute(text("ALTER TABLE site ADD COLUMN application_type VARCHAR(40) NOT NULL DEFAULT 'custom'"))
            if 'installation_mode' not in site_columns:
                with db.engine.begin() as conn:
                    conn.execute(text("ALTER TABLE site ADD COLUMN installation_mode VARCHAR(20) NOT NULL DEFAULT 'automatic'"))
            if 'provisioning_phase' not in site_columns:
                with db.engine.begin() as conn:
                    conn.execute(text("ALTER TABLE site ADD COLUMN provisioning_phase VARCHAR(40) NOT NULL DEFAULT 'ready'"))

        if 'application_access' in inspector.get_table_names():
            access_columns = {column['name'] for column in inspector.get_columns('application_access')}
            with db.engine.begin() as conn:
                if 'wordpress_permissions_user_json' not in access_columns:
                    conn.execute(text("ALTER TABLE application_access ADD COLUMN wordpress_permissions_user_json TEXT NOT NULL DEFAULT '[]'"))
                if 'wordpress_permissions_developer_json' not in access_columns:
                    conn.execute(text("ALTER TABLE application_access ADD COLUMN wordpress_permissions_developer_json TEXT NOT NULL DEFAULT '[]'"))

        if 'database_resource' in inspector.get_table_names():
            resource_columns = {column['name'] for column in inspector.get_columns('database_resource')}
            with db.engine.begin() as conn:
                if 'display_name' not in resource_columns:
                    conn.execute(text("ALTER TABLE database_resource ADD COLUMN display_name VARCHAR(80) NOT NULL DEFAULT 'database'"))
                if 'updated_at' not in resource_columns:
                    conn.execute(text("ALTER TABLE database_resource ADD COLUMN updated_at DATETIME"))

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
    print(f'Bootstrap admin user created: {username}', flush=True)
    return user


def ensure_application_access_registry():
    seeded = False
    for site in Site.query.order_by(Site.id.asc()).all():
        access = ApplicationAccess.query.filter_by(site_id=site.id).first()
        if access:
            continue
        default_root = os.path.join(app.config['UPLOAD_FOLDER'], site.folder_name)
        default_backup_root = scoped_site_backup_root(site)
        db.session.add(ApplicationAccess(
            site_id=site.id,
            owner_user_id=site.user_id,
            assigned_users_json='[]',
            assigned_developers_json='[]',
            permissions_user_json=json.dumps(default_permissions_for_role('user')),
            permissions_developer_json=json.dumps(default_permissions_for_role('developer')),
            wordpress_permissions_user_json=json.dumps([WORDPRESS_UPLOADS_PERMISSION]),
            wordpress_permissions_developer_json=json.dumps(sorted(WORDPRESS_PERMISSION_SET)),
            file_root=default_root,
            upload_root=default_root,
            deployment_root=default_root,
            backup_root=default_backup_root,
        ))
        seeded = True
    if seeded:
        db.session.commit()


def create_or_reset_admin_user(username='developer', password=None, email=None, force=False):
    if password is None:
        password = secrets.token_urlsafe(16)
    if email is None:
        email = f'{username}@localhost'
    with app.app_context():
        user = User.query.filter((User.username == username) | (func.lower(User.email) == username)).first()
        if user is None:
            user = User(username=username, first_name='Developer', last_name='Admin', phone='0000000000', email=email, password=generate_password_hash(password), is_admin=True, must_change_password=True)
            db.session.add(user)
            print(f'Created admin user: {username}', flush=True)
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
                print(f'Reset admin user: {username}', flush=True)
            else:
                print(f'Admin user already exists: {username}', flush=True)
        db.session.commit()
        return user, password


def sanitize_next_path(next_url, fallback):
    if not next_url:
        return fallback
    parsed = urllib.parse.urlparse(next_url)
    if parsed.scheme or parsed.netloc:
        return fallback
    if not next_url.startswith('/'):
        return fallback
    return next_url


def is_signed_webhook_secret_valid(request_obj, expected_secret, header_name='X-Webhook-Secret'):
    supplied = (request_obj.headers.get(header_name) or '').strip()
    return bool(expected_secret and supplied and secrets.compare_digest(expected_secret, supplied))


def github_signature_valid(request_obj, expected_secret):
    signature = (request_obj.headers.get('X-Hub-Signature-256') or '').strip()
    body = request_obj.get_data(cache=True) or b''
    expected = 'sha256=' + hmac.new(expected_secret.encode('utf-8'), body, hashlib.sha256).hexdigest()
    return bool(signature and secrets.compare_digest(signature, expected))


def external_webhook_rate_allowed(kind):
    client = request.headers.get('CF-Connecting-IP') or request.remote_addr or 'unknown'
    key = f'{kind}:{client}'
    now = time.monotonic()
    attempts = webhook_attempts[key]
    while attempts and now - attempts[0] > WEBHOOK_RATE_WINDOW_SECONDS:
        attempts.popleft()
    if len(attempts) >= WEBHOOK_RATE_MAX_REQUESTS:
        return False
    attempts.append(now)
    return True


def notification_test_rate_allowed(provider):
    key = f"{session.get('user_id', 'anonymous')}:{provider}:{request.remote_addr or 'unknown'}"
    now = time.monotonic()
    attempts = notification_test_attempts[key]
    while attempts and now - attempts[0] > 600:
        attempts.popleft()
    if len(attempts) >= 5:
        return False
    attempts.append(now)
    return True


with app.app_context():
    ensure_default_admin_user()
    ensure_application_access_registry()


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
        user = db.session.get(User, session['user_id'])
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
        'capabilities': user_capabilities(user),
    }


@app.before_request
def prepare_security_context():
    g.csp_nonce = secrets.token_urlsafe(16)
    g.request_id = secrets.token_hex(8)


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
        if request.path.startswith('/api/') and request.path in API_CSRF_EXEMPT_PATHS:
            return
        supplied = request.form.get('_csrf_token') or request.headers.get('X-CSRF-Token')
        expected = session.get('_csrf_token')
        if not expected or not supplied or not secrets.compare_digest(expected, supplied):
            abort(403, 'Недійсний CSRF-токен')


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
    response.headers['X-Request-ID'] = getattr(g, 'request_id', '')
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


@app.errorhandler(HTTPException)
def structured_http_error(error):
    if not request.path.startswith('/api/'):
        return error
    code = error.name.upper().replace(' ', '_')
    return jsonify({'error': {
        'code': code,
        'message': error.description,
        'details': None,
        'requestId': getattr(g, 'request_id', ''),
    }}), error.code


@app.errorhandler(500)
def structured_internal_error(error):
    if not request.path.startswith('/api/'):
        return error
    return jsonify({'error': {
        'code': 'INTERNAL_SERVER_ERROR',
        'message': 'Не вдалося завантажити дані таблиці.',
        'details': None,
        'requestId': getattr(g, 'request_id', ''),
    }}), 500


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
        project = parts[5] if len(parts) > 5 else ''
        state = 'running' if status.lower().startswith('up') else 'stopped'
        containers.append({
            'id': container_id,
            'name': name,
            'status': status,
            'state': state,
            'image': image,
            'ports': ports,
            'project': project,
        })
    return containers


def list_docker_containers():
    code, output = run_command(['docker', 'ps', '-a', '--format', '{{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}\t{{.Label "com.docker.compose.project"}}'], timeout=15)
    if code != 0:
        return []
    return parse_docker_container_output(output)


def actual_site_runtime_status(site):
    """Combine site metadata, compose ownership, container state and readiness."""
    if site.is_banned:
        return 'stopped'
    access = ApplicationAccess.query.filter_by(site_id=site.id).first()
    metadata_path = os.path.join(access.deployment_root or '', 'runtime.json') if access else ''
    try:
        with open(metadata_path, encoding='utf-8') as handle:
            metadata = json.load(handle)
            project = str(metadata.get('project') or '')
    except (OSError, ValueError, TypeError):
        return 'needs_setup' if site.application_type == 'wordpress' else 'not deployed'
    if not project:
        return 'not deployed'
    project_containers = [item for item in list_docker_containers() if item.get('project') == project]
    if not project_containers:
        return 'stopped'
    if any('unhealthy' in item.get('status', '').lower() or item.get('state') != 'running' for item in project_containers):
        return 'error'
    ready = healthcheck(metadata, timeout=3)
    if site.application_type == 'wordpress':
        access = access or ensure_application_access(site)
        state = detect_wordpress_state(site, access)
        if state.get('code') != 'installed':
            return 'needs_setup'
    if not ready.get('ok'):
        return 'error'
    return 'running'


def actual_site_runtime_statuses(sites):
    return {site.id: actual_site_runtime_status(site) for site in sites}


def list_scoped_docker_containers(user):
    containers = list_docker_containers()
    if not user:
        return []
    if user_role(user) == 'admin' or user.is_admin:
        return containers

    site_ids = set(assigned_application_ids(user))
    if not site_ids:
        return []

    sites = Site.query.filter(Site.id.in_(site_ids)).all()
    allowed_projects = set()
    allowed_tokens = set()
    for site in sites:
        if site.name:
            allowed_projects.add(site.name.lower())
            allowed_tokens.add(site.name.lower())
        if site.folder_name:
            allowed_tokens.add(site.folder_name.lower())

    scoped = []
    for container in containers:
        name = (container.get('name') or '').lower()
        project = (container.get('project') or '').lower()
        if project and project in allowed_projects:
            scoped.append(container)
            continue
        if any(token and token in name for token in allowed_tokens):
            scoped.append(container)
    return scoped


def can_manage_site(user, site):
    return bool(user and site and (user.is_admin or site.user_id == user.id))


def parse_json_list(raw, cast=int):
    try:
        values = json.loads(raw or '[]')
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(values, list):
        return []
    parsed = []
    for item in values:
        try:
            parsed.append(cast(item))
        except (TypeError, ValueError):
            continue
    return parsed


def dump_json_list(values):
    return json.dumps(list(dict.fromkeys(values)))


def user_role(user):
    if not user:
        return 'anonymous'
    role = (user.role or '').strip().lower()
    if role in {'user', 'developer', 'admin', 'viewer'}:
        return role
    return 'admin' if user.is_admin else 'viewer'


def role_permission_set(role):
    if role == 'admin':
        return ADMIN_PERMISSION_SET
    if role == 'developer':
        return DEVELOPER_PERMISSION_SET
    if role == 'viewer':
        return set()
    return USER_PERMISSION_SET


def user_has_role_permission(user, permission):
    if not user:
        return False
    if user.is_admin or user_role(user) == 'admin':
        return True
    return permission in role_permission_set(user_role(user))


def user_capabilities(user):
    """Stable product capabilities derived from backend authorization policy."""
    checks = {
        'canCreateSite': 'site.create',
        'canRestartSite': 'site.manage',
        'canCreateDatabase': 'database.create',
        'canUseSftp': 'sftp.access',
        'canCreateBackup': 'backup.create',
        'canRestoreBackup': 'backup.restore',
        'canManageDomain': 'domain.manage',
        'canDeployGit': 'git.connect',
        'canDeployGithub': 'git.connect',
        'canViewLogs': 'logs.view',
    }
    capabilities = {name: user_has_role_permission(user, permission) for name, permission in checks.items()}
    return capabilities


def require_role_permission(user, permission):
    if not user_has_role_permission(user, permission):
        abort(403)


def available_service_catalog(user, language='uk'):
    items = []
    actionable_slugs = {'static-site', 'connect-git', 'create-sftp', 'configure-backups', 'php-health'}
    for item in SERVICE_CATALOG:
        permission = item.get('permission')
        allowed = not permission or user_has_role_permission(user, permission)
        lang_key = 'en' if language == 'en' else 'uk'
        title = item['title_en'] if language == 'en' else item['title_uk']
        summary = item.get('summary_en') if language == 'en' else item.get('summary_uk')
        category_key = item.get('category', 'operations')
        status_key = item.get('status', 'partial')
        items.append({
            'slug': item['slug'],
            'title': title,
            'summary': summary or '',
            'category': category_key,
            'category_label': SERVICE_CATALOG_CATEGORIES.get(category_key, SERVICE_CATALOG_CATEGORIES['operations']).get(lang_key, category_key),
            'status': status_key,
            'status_label': SERVICE_CATALOG_STATUS.get(status_key, SERVICE_CATALOG_STATUS['partial']).get(lang_key, status_key.upper()),
            'site_type': item.get('site_type'),
            'permission': permission,
            'allowed': allowed,
            'actionable': allowed and (
                item['slug'] in actionable_slugs
                or (item['slug'] == 'connect-domain' and (user.is_admin or user_role(user) == 'admin'))
            ),
        })
    return items


def default_permissions_for_role(role):
    if role == 'admin':
        return sorted(ADMIN_PERMISSION_SET)
    if role == 'developer':
        return sorted(DEVELOPER_PERMISSION_SET)
    if role == 'viewer':
        return []
    return sorted(USER_PERMISSION_SET)


def normalize_permission_list(values, role):
    requested = set(values or [])
    if role == 'developer':
        allowed = DEVELOPER_PERMISSION_SET
    elif role == 'admin':
        allowed = ADMIN_PERMISSION_SET
    elif role == 'viewer':
        allowed = set()
    else:
        allowed = USER_PERMISSION_SET
    cleaned = sorted(item for item in requested if item in allowed)
    return cleaned or default_permissions_for_role(role)


def ensure_application_access(site):
    access = ApplicationAccess.query.filter_by(site_id=site.id).first()
    if access:
        changed = False
        default_file_root = os.path.join(app.config['UPLOAD_FOLDER'], site.folder_name)
        default_backup_root = scoped_site_backup_root(site)
        if not access.file_root:
            access.file_root = default_file_root
            changed = True
        if not access.upload_root:
            access.upload_root = default_file_root
            changed = True
        if not access.deployment_root:
            access.deployment_root = default_file_root
            changed = True
        if not access.backup_root:
            access.backup_root = default_backup_root
            changed = True
        if not (access.wordpress_permissions_user_json or '').strip():
            access.wordpress_permissions_user_json = json.dumps([WORDPRESS_UPLOADS_PERMISSION])
            changed = True
        if not (access.wordpress_permissions_developer_json or '').strip():
            access.wordpress_permissions_developer_json = json.dumps(sorted(WORDPRESS_PERMISSION_SET))
            changed = True
        if changed:
            db.session.commit()
        return access
    default_root = os.path.join(app.config['UPLOAD_FOLDER'], site.folder_name)
    default_backup_root = scoped_site_backup_root(site)
    access = ApplicationAccess(
        site_id=site.id,
        owner_user_id=site.user_id,
        assigned_users_json='[]',
        assigned_developers_json='[]',
        permissions_user_json=json.dumps(default_permissions_for_role('user')),
        permissions_developer_json=json.dumps(default_permissions_for_role('developer')),
        wordpress_permissions_user_json=json.dumps([WORDPRESS_UPLOADS_PERMISSION]),
        wordpress_permissions_developer_json=json.dumps(sorted(WORDPRESS_PERMISSION_SET)),
        file_root=default_root,
        upload_root=default_root,
        deployment_root=default_root,
        backup_root=default_backup_root,
    )
    db.session.add(access)
    db.session.commit()
    return access


def assigned_application_ids(user):
    if not user:
        return []
    role = user_role(user)
    if role == 'admin':
        return [site.id for site in Site.query.order_by(Site.id.desc()).all()]
    collected = []
    for site in Site.query.order_by(Site.id.desc()).all():
        access = ensure_application_access(site)
        if site.user_id == user.id:
            collected.append(site.id)
            continue
        user_ids = parse_json_list(access.assigned_users_json, int)
        dev_ids = parse_json_list(access.assigned_developers_json, int)
        if role == 'developer' and user.id in dev_ids:
            collected.append(site.id)
        elif role == 'user' and user.id in user_ids:
            collected.append(site.id)
    return collected


def user_has_application_assignment(user, access):
    if not user or not access:
        return False
    role = user_role(user)
    if role == 'admin' or user.is_admin:
        return True
    if access.owner_user_id == user.id:
        return True
    if role == 'developer':
        return user.id in parse_json_list(access.assigned_developers_json, int)
    return user.id in parse_json_list(access.assigned_users_json, int)


def user_permissions_for_application(user, access):
    role = user_role(user)
    if role == 'admin' or user.is_admin:
        return set(ADMIN_PERMISSION_SET)
    if access.owner_user_id == user.id:
        return set(USER_PERMISSION_SET)
    if role == 'developer':
        return set(parse_json_list(access.permissions_developer_json, str))
    return set(parse_json_list(access.permissions_user_json, str))


def wordpress_permissions_for_application(user, access):
    role = user_role(user)
    if role == 'admin' or user.is_admin:
        return set(WORDPRESS_PERMISSION_SET)
    if access.owner_user_id == user.id:
        return {WORDPRESS_UPLOADS_PERMISSION}
    if role == 'developer':
        return set(parse_json_list(access.wordpress_permissions_developer_json, str))
    return set(parse_json_list(access.wordpress_permissions_user_json, str))


def is_wordpress_site_root(root_path):
    return os.path.isfile(os.path.join(root_path, 'wp-config.php')) or os.path.isdir(os.path.join(root_path, 'wp-content'))


def normalized_relative_path(relative_path):
    return (relative_path or '').replace('\\', '/').lstrip('./').strip('/')


def wordpress_permission_for_relative_path(relative_path):
    rel = normalized_relative_path(relative_path).lower()
    if not rel:
        return None
    if rel == 'wp-content/uploads' or rel.startswith('wp-content/uploads/'):
        return WORDPRESS_UPLOADS_PERMISSION
    if rel == 'wp-content/themes' or rel.startswith('wp-content/themes/'):
        return WORDPRESS_THEMES_PERMISSION
    if rel == 'wp-content/plugins' or rel.startswith('wp-content/plugins/'):
        return WORDPRESS_PLUGINS_PERMISSION
    return None


def enforce_wordpress_path_permission(user, access, root_path, relative_path):
    if not is_wordpress_site_root(root_path):
        return
    needed = wordpress_permission_for_relative_path(relative_path)
    if not needed:
        return
    if needed not in wordpress_permissions_for_application(user, access):
        abort(403)


def directory_size_safe(path):
    try:
        return directory_size(path)
    except OSError:
        return 0


def backup_usage_bytes_for_access(access):
    return directory_size_safe(application_root(access, bucket='backup'))


def application_usage_bytes_for_access(access):
    return directory_size_safe(application_root(access, bucket='file'))


def quota_snapshot(access):
    app_used = application_usage_bytes_for_access(access)
    backup_used = backup_usage_bytes_for_access(access)
    app_limit = max(64, int(access.application_quota_mb or 64)) * 1024 * 1024
    backup_limit = max(64, int(access.backup_quota_mb or 64)) * 1024 * 1024
    return {
        'application_used_bytes': app_used,
        'application_limit_bytes': app_limit,
        'backup_used_bytes': backup_used,
        'backup_limit_bytes': backup_limit,
        'application_ratio': round((app_used / app_limit) * 100, 2) if app_limit else 0,
        'backup_ratio': round((backup_used / backup_limit) * 100, 2) if backup_limit else 0,
    }


def enforce_backup_quota(access, additional_bytes=0):
    snapshot = quota_snapshot(access)
    if snapshot['backup_used_bytes'] + max(0, additional_bytes) > snapshot['backup_limit_bytes']:
        raise ValueError('Backup quota exceeded for this application')


def enforce_application_quota(access, resulting_bytes):
    snapshot = quota_snapshot(access)
    if max(0, resulting_bytes) > snapshot['application_limit_bytes']:
        raise ValueError('Application quota exceeded for this application')


def estimate_zip_unpacked_bytes(zip_path):
    with zipfile.ZipFile(zip_path, 'r') as archive:
        return sum(item.file_size for item in archive.infolist())


def append_jsonl(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a', encoding='utf-8') as handle:
        handle.write(json.dumps(data, ensure_ascii=True) + '\n')


def normalize_sftp_password_hash(value):
    cleaned = (value or '').strip()
    if not cleaned or ':' in cleaned:
        return None
    if not cleaned.startswith('$'):
        return None
    return cleaned


def build_sftp_password_hash(plain_password):
    if not plain_password:
        return None
    try:
        proc = subprocess.run(
            ['openssl', 'passwd', '-6', '-stdin'],
            input=f'{plain_password}\n',
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        hashed = (proc.stdout or '').strip() if proc.returncode == 0 else ''
    except Exception:
        return None
    return normalize_sftp_password_hash(hashed)


def queue_sftp_provision(account, action, actor='system'):
    application_ids = parse_json_list(account.assigned_applications_json, int)
    application_roots = []
    if application_ids:
        for assigned_site in Site.query.filter(Site.id.in_(application_ids)).all():
            assigned_access = ensure_application_access(assigned_site)
            application_roots.append({
                'site_id': assigned_site.id,
                'folder_name': assigned_site.folder_name,
                'file_root': application_root(assigned_access, bucket='file'),
            })
    payload = {
        'timestamp': datetime.now().isoformat(),
        'action': action,
        'account_id': account.id,
        'username': account.username,
        'role': account.role,
        'assigned_user_id': account.assigned_user_id,
        'assigned_applications': application_ids,
        'application_roots': application_roots,
        'chroot_directory': account.chroot_directory,
        'enabled': bool(account.enabled),
        'auth_type': account.auth_type,
        'password_hash': normalize_sftp_password_hash(account.password_hash),
        'public_keys': parse_json_list(account.public_keys_json, str),
        'actor': actor,
    }
    with SFTP_PROVISION_LOCK:
        append_jsonl(SFTP_PROVISION_QUEUE_FILE, payload)
    account.system_state = 'queued'
    db.session.commit()
    # The privileged worker is triggered by myh-sftp-provision.path when the
    # queue file changes. This remains compatible with NoNewPrivileges=true.


def apply_sftp_provision_results():
    if not os.path.isfile(SFTP_PROVISION_RESULT_FILE):
        return 0
    try:
        with open(SFTP_PROVISION_RESULT_FILE, 'r', encoding='utf-8') as handle:
            rows = [line.strip() for line in handle.readlines() if line.strip()]
    except OSError:
        return 0
    if not rows:
        return 0
    try:
        with open(SFTP_PROVISION_RESULT_FILE, 'w', encoding='utf-8') as handle:
            handle.write('')
    except OSError:
        return 0

    applied = 0
    for row in rows:
        try:
            item = json.loads(row)
        except json.JSONDecodeError:
            continue
        account_id = int(item.get('account_id') or 0)
        account = db.session.get(SftpAccount, account_id) if account_id else None
        if not account:
            continue
        status = (item.get('status') or 'error').strip().lower()
        account.system_state = 'active' if status == 'ok' else status[:40]
        detail = (item.get('message') or '')[:500]
        action = (item.get('action') or 'sync')[:40]
        record_sftp_audit(account, f'provision_{action}', status='success' if status == 'ok' else 'failed', detail=detail)
        applied += 1
    if applied:
        db.session.commit()
    return applied


def application_root(access, bucket='file'):
    if bucket == 'upload':
        root = access.upload_root
    elif bucket == 'deployment':
        root = access.deployment_root
    elif bucket == 'backup':
        root = access.backup_root
    else:
        root = access.file_root
    if not root:
        root = access.file_root
    return os.path.realpath(root)


def safe_resource_path(access, relative_path='', bucket='file'):
    root = application_root(access, bucket=bucket)
    target = os.path.realpath(os.path.join(root, relative_path or ''))
    if os.path.commonpath([root, target]) != root:
        abort(400, 'Недійсний шлях')
    return target


def require_application_permission(user, site, permission):
    access = ensure_application_access(site)
    if not user_has_application_assignment(user, access):
        abort(403)
    if permission not in user_permissions_for_application(user, access):
        abort(403)
    return access


def record_upload_history(site, user, filename, size_bytes=0, source='panel', status='success', deployment='', detail=''):
    entry = UploadHistory(
        application_id=site.id if site else None,
        user_id=user.id if user else None,
        username=user.username if user else 'system',
        role=user_role(user),
        filename=(filename or '')[:255],
        size_bytes=max(0, int(size_bytes or 0)),
        source=(source or 'panel')[:20],
        status=(status or 'success')[:20],
        deployment=(deployment or '')[:120],
        detail=(detail or '')[:500],
    )
    db.session.add(entry)
    db.session.commit()


def record_sftp_audit(account, action, status='success', detail=''):
    entry = SftpAuditEvent(
        sftp_account_id=account.id if account else None,
        username=(account.username if account else '')[:80],
        action=(action or '')[:60],
        status=(status or 'success')[:20],
        detail=(detail or '')[:500],
    )
    db.session.add(entry)
    db.session.commit()


def application_summary_for_user(user, site):
    access = ensure_application_access(site)
    quotas = quota_snapshot(access)
    return {
        'id': site.id,
        'name': site.name,
        'folder_name': site.folder_name,
        'owner': site.owner.username if site.owner else '—',
        'file_root': access.file_root,
        'upload_root': access.upload_root,
        'deployment_root': access.deployment_root,
        'backup_root': access.backup_root,
        'user_permissions': parse_json_list(access.permissions_user_json, str),
        'developer_permissions': parse_json_list(access.permissions_developer_json, str),
        'wordpress_permissions_user': parse_json_list(access.wordpress_permissions_user_json, str),
        'wordpress_permissions_developer': parse_json_list(access.wordpress_permissions_developer_json, str),
        'storage_quota_mb': access.storage_quota_mb,
        'backup_quota_mb': access.backup_quota_mb,
        'application_quota_mb': access.application_quota_mb,
        'max_file_size_mb': access.max_file_size_mb,
        'max_upload_size_mb': access.max_upload_size_mb,
        'quota_application_ratio': quotas['application_ratio'],
        'quota_backup_ratio': quotas['backup_ratio'],
    }


def sftp_connection_host():
    if SFTP_CONNECTION_MODE == 'cloudflare':
        return 'localhost'
    configured = os.environ.get('HOSTING_PANEL_SFTP_HOST', '').strip()
    if configured:
        return configured
    host = request.host.split(':', 1)[0]
    if host.endswith('.myh.guru') or host == 'myh.guru':
        for probe in ('https://ifconfig.me', 'https://api.ipify.org'):
            try:
                with urllib.request.urlopen(probe, timeout=2) as response:
                    candidate = (response.read().decode('utf-8', errors='ignore') or '').strip()
                if re.fullmatch(r'(?:\d{1,3}\.){3}\d{1,3}', candidate):
                    return candidate
            except Exception:
                continue
    return host


def sftp_filezilla_payload(account):
    app_ids = parse_json_list(account.assigned_applications_json, int)
    assigned_sites = Site.query.filter(Site.id.in_(app_ids)).order_by(Site.name.asc()).all() if app_ids else []
    return {
        'protocol': 'SFTP',
        'host': sftp_connection_host(),
        'port': SFTP_LOCAL_PORT if SFTP_CONNECTION_MODE == 'cloudflare' else 22,
        'username': account.username,
        'root': account.chroot_directory,
        'auth_type': account.auth_type,
        'assigned_sites': assigned_sites,
    }


def choose_user_sftp_chroot(user, site_ids):
    # OpenSSH requires every ChrootDirectory path component to be owned by
    # root and not writable by the SFTP account. Site roots live below the
    # panel user's home, so they cannot safely be used as chroot roots.
    return os.path.join('/srv/apps', user.username)


def get_or_prepare_personal_sftp_account(user):
    account = SftpAccount.query.filter_by(assigned_user_id=user.id).order_by(SftpAccount.id.asc()).first()
    by_username = SftpAccount.query.filter_by(username=user.username).first()
    if by_username and by_username.assigned_user_id not in {None, user.id}:
        return None, 'SFTP login зайнятий. Зверніться до адміністратора.'
    if not account and by_username:
        account = by_username

    site_ids = assigned_application_ids(user)
    if not site_ids:
        return None, 'Створіть або отримайте доступ до сайту перед увімкненням SFTP.'

    compatible_hash = normalize_sftp_password_hash(user.password)
    if not account:
        account = SftpAccount(
            username=user.username,
            role='user',
            assigned_user_id=user.id,
            assigned_applications_json=dump_json_list(site_ids),
            chroot_directory=choose_user_sftp_chroot(user, site_ids),
            auth_type='password',
            password_hash=compatible_hash,
            enabled=False,
            system_state='pending',
        )
        db.session.add(account)
        db.session.commit()
        return account, None

    account.username = user.username
    account.role = 'user'
    account.assigned_user_id = user.id
    account.assigned_applications_json = dump_json_list(site_ids)
    account.chroot_directory = choose_user_sftp_chroot(user, site_ids)
    account.auth_type = 'password'
    if compatible_hash:
        account.password_hash = compatible_hash
    db.session.commit()
    return account, None


_LOG_SECRET_PATTERN = re.compile(r'(password|token|authorization|cookie|set-cookie|jwt|api[-_ ]?key|secret|db[_-]?pass)', re.IGNORECASE)


def mask_sensitive_text(line):
    if not line:
        return line
    value = str(line)
    value = re.sub(r'-----BEGIN [^-]+PRIVATE KEY-----[\s\S]*?-----END [^-]+PRIVATE KEY-----', '[PRIVATE KEY REDACTED]', value, flags=re.I)
    value = re.sub(r'(?i)\b(authorization\s*:\s*)(?:bearer|basic)?\s*[^\s,;]+', r'\1********', value)
    value = re.sub(r'(?i)\b(cookie|set-cookie)\s*:\s*[^\r\n]+', r'\1: ********', value)
    value = re.sub(r'(?i)\b(password|token|jwt|api[-_ ]?key|secret|db[_-]?pass)\b\s*[:=]\s*([^\s,;]+|"[^"]*"|\'[^\']*\')', r'\1=********', value)
    value = re.sub(r'(?i)(mysql(?:\+\w+)?://[^:/\s]+:)[^@\s]+(@)', r'\1********\2', value)
    return value


def zip_single_root_folder(members):
    roots = set()
    has_root_file = False
    for member in members:
        raw_name = (member.filename or '').replace('\\', '/').strip('/')
        if not raw_name:
            continue
        parts = [part for part in raw_name.split('/') if part]
        if not parts:
            continue
        roots.add(parts[0])
        if len(parts) == 1 and not member.is_dir():
            has_root_file = True
        if len(roots) > 1:
            return None
    if has_root_file or not roots:
        return None
    return next(iter(roots))


def safe_extract_zip(archive, destination, strip_prefix=''):
    destination = os.path.realpath(destination)
    total_uncompressed = 0
    members = archive.infolist()
    prefix = (strip_prefix or '').replace('\\', '/').strip('/')
    prefix_with_sep = f'{prefix}/' if prefix else ''
    if len(members) > ARCHIVE_MAX_ENTRIES:
        raise ValueError('Архів містить забагато файлів')
    for member in members:
        name = (member.filename or '').replace('\\', '/').strip()
        if not name:
            continue
        normalized = os.path.normpath(name).replace('\\', '/')
        if normalized.startswith('/') or normalized.startswith('../') or normalized == '..' or re.match(r'^[A-Za-z]:', normalized):
            raise ValueError('Архів містить небезпечний шлях')
        output_rel = normalized
        if prefix:
            if normalized == prefix:
                continue
            if not normalized.startswith(prefix_with_sep):
                raise ValueError('Архів має некоректну структуру для clean mode')
            output_rel = normalized[len(prefix_with_sep):]
            if not output_rel:
                continue
        mode = (member.external_attr >> 16) & 0o170000
        if mode in {0o120000, 0o060000}:
            raise ValueError('Архів містить заборонений тип запису')
        total_uncompressed += max(0, int(member.file_size or 0))
        if total_uncompressed > ARCHIVE_MAX_UNCOMPRESSED_BYTES:
            raise ValueError('Архів перевищує ліміт розпакування')
        if member.compress_size and member.file_size > member.compress_size * ARCHIVE_MAX_COMPRESSION_RATIO:
            raise ValueError('Архів має небезпечний коефіцієнт стиснення')
        target = os.path.realpath(os.path.join(destination, output_rel))
        if os.path.commonpath([destination, target]) != destination:
            raise ValueError('Архів містить небезпечний шлях')
        if not member.is_dir() and os.path.exists(target):
            raise ValueError('Архів не може мовчки перезаписати існуючий файл')

    for member in members:
        name = (member.filename or '').replace('\\', '/').strip()
        if not name:
            continue
        normalized = os.path.normpath(name).replace('\\', '/')
        output_rel = normalized
        if prefix:
            if normalized == prefix:
                continue
            if not normalized.startswith(prefix_with_sep):
                continue
            output_rel = normalized[len(prefix_with_sep):]
            if not output_rel:
                continue
        target = os.path.realpath(os.path.join(destination, output_rel))
        if member.is_dir():
            os.makedirs(target, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with archive.open(member, 'r') as source_handle, open(target, 'wb') as target_handle:
            shutil.copyfileobj(source_handle, target_handle)


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


def ensure_cloudflare_domain_record(domain, remove=False):
    zone_name = get_cloudflare_zone_name().lower().rstrip('.')
    domain = domain.lower().rstrip('.')
    if domain != zone_name and not domain.endswith('.' + zone_name):
        return False, 'Domain is outside the managed Cloudflare zone.'
    zone_id, payload = get_cloudflare_zone_id(zone_name)
    if not zone_id: return False, str(payload.get('errors') or 'Cloudflare zone unavailable')
    records, listing = cloudflare_dns_records(zone_id, 'CNAME')
    if not listing.get('success'): return False, str(listing.get('errors') or 'DNS listing failed')
    existing = next((item for item in records if str(item.get('name','')).lower().rstrip('.') == domain), None)
    if remove:
        if not existing: return True, 'already absent'
        result = cloudflare_request('DELETE', f'/zones/{zone_id}/dns_records/{existing["id"]}')
    else:
        data = {'type':'CNAME','name':domain,'content':'myh.guru','ttl':1,'proxied':True}
        result = cloudflare_request('PUT' if existing else 'POST', f'/zones/{zone_id}/dns_records/{existing["id"]}' if existing else f'/zones/{zone_id}/dns_records', data)
    return bool(result.get('success')), str(result.get('errors') or 'ok')


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


def collect_application_logs_for_user(user, lines=200):
    if not user:
        return []
    site_ids = assigned_application_ids(user)
    if not site_ids:
        return []

    max_lines = max(50, min(int(lines or 200), 500))
    ring = deque(maxlen=max_lines)
    sites = Site.query.filter(Site.id.in_(site_ids)).order_by(Site.name.asc()).all()
    for site in sites:
        log_path = os.path.join(app.instance_path, 'deploy_logs', f'{site.name}.log')
        if not os.path.isfile(log_path):
            continue
        try:
            with open(log_path, 'r', encoding='utf-8', errors='ignore') as handle:
                for line in handle:
                    ring.append(f'[{site.name}] {line.rstrip()}')
        except OSError:
            continue
    return [mask_sensitive_text(item) for item in list(ring)]


def classify_log_issues(lines):
    """Return deterministic, deduplicated guidance for common hosting failures."""
    rules = (
        ('port', ('connection refused', 'econnrefused', 'port is not listening', 'failed to connect'),
         'Application port is not responding.', 'Verify that the process listens on port 8080.'),
        ('environment', ('missing environment', 'environment variable', 'keyerror:', 'undefined variable'),
         'A required environment variable may be missing.', 'Check Site settings → Environment and restart the application.'),
        ('dependencies', ('npm err', 'pip failed', 'could not find a version', 'module not found', 'modulenotfounderror'),
         'Dependency installation failed.', 'Review the dependency manifest and the first installation error.'),
        ('permissions', ('permission denied', 'eacces', 'operation not permitted'),
         'The application encountered a file permission error.', 'Check that the application writes only inside its assigned directories.'),
        ('database', ('database connection refused', 'sqlstate', 'access denied for user', 'could not connect to server'),
         'The application could not connect to its database.', 'Verify database status and the write-only connection environment values.'),
        ('disk', ('no space left on device', 'disk quota exceeded'),
         'Storage is full or the site quota was reached.', 'Remove unused files/backups or review the account storage quota.'),
    )
    combined = '\n'.join(lines).lower()
    return [
        {'code': code, 'summary': summary, 'next_step': next_step}
        for code, needles, summary, next_step in rules if any(needle in combined for needle in needles)
    ]


def scaffold_site_content(site_path, subdomain):
    default_index = os.path.join(site_path, 'index.html')
    if not os.path.exists(default_index):
        with open(default_index, 'w', encoding='utf-8') as f:
            f.write(f"<h1>{subdomain}.myh.guru працює</h1><p>Завантажте файли статичного сайту через файловий менеджер.</p>")


def ensure_php_site_bootstrap(site_path, site_name, runtime_version='8.2'):
    os.makedirs(site_path, exist_ok=True)
    index_php = os.path.join(site_path, 'index.php')
    if os.path.exists(index_php):
        return index_php
    with open(index_php, 'w', encoding='utf-8') as handle:
        handle.write("<?php\n")
        handle.write("http_response_code(200);\n")
        handle.write("header('Content-Type: text/html; charset=utf-8');\n")
        handle.write(f"$site = {site_name!r};\n")
        handle.write(f"$runtime = {runtime_version!r};\n")
        handle.write("echo '<h1>' . htmlspecialchars($site, ENT_QUOTES, 'UTF-8') . ' is running</h1>';\n")
        handle.write("echo '<p>PHP runtime target: ' . htmlspecialchars($runtime, ENT_QUOTES, 'UTF-8') . '</p>';\n")
    return index_php


def read_php_runtime_from_stack(access):
    stack_root = application_root(access, bucket='deployment')
    metadata_path = os.path.join(stack_root, 'panel-metadata.json')
    if not os.path.isfile(metadata_path):
        return None
    try:
        with open(metadata_path, 'r', encoding='utf-8') as meta_handle:
            payload = json.load(meta_handle)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    runtime = payload.get('runtime') or {}
    language = (runtime.get('language') or '').strip().lower()
    version = (runtime.get('version') or '').strip()
    if language != 'php' or not version:
        return None
    return version


def parse_php_version_string(raw_output):
    if not raw_output:
        return None
    match = re.search(r'PHP\s+(\d+\.\d+(?:\.\d+)?)', raw_output)
    return match.group(1) if match else None


def php_runtime_compatible(required_version, installed_version):
    if not required_version or not installed_version:
        return False
    required_parts = required_version.split('.')
    installed_parts = installed_version.split('.')
    if len(required_parts) < 2 or len(installed_parts) < 2:
        return False
    return required_parts[0] == installed_parts[0] and required_parts[1] == installed_parts[1]


def scaffold_application_stack(folder_name, site_type, source_mode='upload', runtime_version=None):
    if site_type not in {'static', 'wordpress', 'node', 'python', 'php'}:
        return None
    os.makedirs(APP_STACKS_ROOT, exist_ok=True)
    stack_root = os.path.join(APP_STACKS_ROOT, folder_name)
    os.makedirs(stack_root, exist_ok=True)

    template_root = os.path.join('/srv/templates', site_type)
    if os.path.isdir(template_root):
        for name in os.listdir(template_root):
            src = os.path.join(template_root, name)
            dst = os.path.join(stack_root, name)
            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)

    metadata_path = os.path.join(stack_root, 'panel-metadata.json')
    metadata = {
        'site_type': site_type,
        'source_mode': source_mode,
        'generated_at': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        'generated_by': 'create-site-wizard',
    }
    if site_type == 'php' and runtime_version:
        metadata['runtime'] = {
            'language': 'php',
            'version': runtime_version,
        }

    with open(metadata_path, 'w', encoding='utf-8') as handle:
        json.dump({
            **metadata,
        }, handle, ensure_ascii=True, indent=2)
    return stack_root


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


def backup_directory(site, access=None):
    access = access or ensure_application_access(site)
    path = application_root(access, bucket='backup') if access.backup_root else scoped_site_backup_root(site)
    os.makedirs(path, exist_ok=True)
    return path


def list_site_backups(site, access=None):
    path = backup_directory(site, access=access)
    backups = []
    for filename in sorted(os.listdir(path), reverse=True):
        if re.fullmatch(r'\d{8}-\d{6}\.zip', filename):
            full_path = os.path.join(path, filename)
            backups.append({'name': filename, 'size': os.path.getsize(full_path), 'created': datetime.fromtimestamp(os.path.getmtime(full_path))})
    return backups


def create_backup_archive(site, access=None):
    access = access or ensure_application_access(site)
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    destination = os.path.join(backup_directory(site, access=access), f'{timestamp}.zip')
    source = application_root(access, bucket='file')
    estimated_size = directory_size_safe(source)
    enforce_backup_quota(access, additional_bytes=estimated_size)
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for root, _, filenames in os.walk(source):
            for filename in filenames:
                full_path = os.path.join(root, filename)
                archive.write(full_path, os.path.relpath(full_path, source))
    try:
        if site.application_type == 'wordpress':
            resource = DatabaseResource.query.filter_by(application_id=site.id, engine='mysql').first()
            if resource:
                database_dump = create_database_backup(resource)
                database_sidecar = destination + '.wordpress.sql'
                checksum_sidecar = database_sidecar + '.sha256'
                os.replace(database_dump, database_sidecar)
                os.replace(database_dump + '.sha256', checksum_sidecar)
                os.chmod(database_sidecar, 0o600)
                os.chmod(checksum_sidecar, 0o600)
    except Exception:
        for candidate in (destination, destination + '.wordpress.sql', destination + '.wordpress.sql.sha256'):
            try:
                os.unlink(candidate)
            except FileNotFoundError:
                pass
        raise
    enforce_backup_quota(access, additional_bytes=0)
    return destination


def validate_wordpress_database_sidecar(site, archive_path):
    if site.application_type != 'wordpress':
        return None
    sidecar = archive_path + '.wordpress.sql'
    checksum = sidecar + '.sha256'
    resource = DatabaseResource.query.filter_by(application_id=site.id, engine='mysql').first()
    if not resource and not os.path.isfile(sidecar) and not os.path.isfile(checksum):
        return None
    if not os.path.isfile(sidecar) or not os.path.isfile(checksum):
        raise RuntimeError('WordPress database backup is missing.')
    if not resource:
        raise RuntimeError('WordPress database resource is missing.')
    with open(checksum, encoding='ascii') as checksum_file:
        expected = checksum_file.read().strip()
    digest = hashlib.sha256()
    with open(sidecar, 'rb') as dump_file:
        for chunk in iter(lambda: dump_file.read(1024 * 1024), b''):
            digest.update(chunk)
    actual = digest.hexdigest()
    if not expected or not secrets.compare_digest(expected, actual):
        raise RuntimeError('WordPress database backup checksum verification failed.')
    return sidecar, resource


def restore_wordpress_database_sidecar(site, archive_path):
    validated = validate_wordpress_database_sidecar(site, archive_path)
    if validated is None:
        return
    sidecar, resource = validated
    defaults = database_client_defaults(resource)
    try:
        with open(sidecar, 'rb') as input_file:
            process = subprocess.run(['mysql', f'--defaults-extra-file={defaults}', resource.database_name], stdin=input_file,
                                     stderr=subprocess.PIPE, timeout=300, check=False)
        if process.returncode:
            raise RuntimeError('WordPress database restore failed.')
    finally:
        os.unlink(defaults)


def reconcile_wordpress_permissions(site, access=None):
    """Grant the panel owner scoped write access without broad world permissions."""
    if site.application_type != 'wordpress':
        return
    access = access or ensure_application_access(site)
    root = application_root(access, bucket='file')
    source_gid = os.stat(root).st_gid
    try:
        os.chmod(root, 0o2770)
        for current_root, directories, filenames in os.walk(root):
            for name in directories:
                path = os.path.join(current_root, name); os.chmod(path, 0o2770); os.chown(path, -1, source_gid)
            for name in filenames:
                path = os.path.join(current_root, name); os.chmod(path, 0o660); os.chown(path, -1, source_gid)
    except OSError as exc:
        raise RuntimeError('Unable to reconcile WordPress file permissions.') from exc


def extract_zip_to_site(zip_path, site_path):
    with zipfile.ZipFile(zip_path, 'r') as archive:
        safe_extract_zip(archive, site_path)


def detect_single_root_folder(directory):
    try:
        entries = [name for name in os.listdir(directory) if name not in {'.', '..'}]
    except OSError:
        return None
    return entries[0] if len(entries) == 1 and os.path.isdir(os.path.join(directory, entries[0])) else None


def build_authenticated_repo_url(repo_url, git_token=None):
    token = (git_token or '').strip()
    if not token:
        return repo_url
    parsed = urllib.parse.urlsplit(repo_url)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        return repo_url
    if '@' in parsed.netloc:
        return repo_url
    encoded_token = urllib.parse.quote(token, safe='')
    auth_netloc = f'x-access-token:{encoded_token}@{parsed.netloc}'
    return urllib.parse.urlunsplit((parsed.scheme, auth_netloc, parsed.path, parsed.query, parsed.fragment))


def sanitize_git_error_output(output, git_token=None):
    text_output = output or ''
    token = (git_token or '').strip()
    if token:
        text_output = text_output.replace(token, '***')
        text_output = text_output.replace(urllib.parse.quote(token, safe=''), '***')
    text_output = re.sub(r'x-access-token:[^@\s]+@', 'x-access-token:***@', text_output)
    return text_output


GIT_PROVIDERS = {'github', 'gitlab', 'bitbucket', 'generic'}
GIT_AUTH_TYPES = {'public', 'pat', 'ssh'}
GIT_BRANCH_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$')
INTEGRATION_SECRETS_ROOT = os.path.join(app.instance_path, 'integration_secrets')
APPLICATION_SECRETS_ROOT = os.path.join(app.instance_path, 'application_secrets')


def validate_git_repository_url(repo_url, provider='generic'):
    value = (repo_url or '').strip()
    if not value or len(value) > 500 or any(char in value for char in '\r\n\x00'):
        return None, 'GIT_URL_INVALID'
    if value.startswith('-'):
        return None, 'GIT_URL_INVALID'
    if re.fullmatch(r'git@[A-Za-z0-9.-]+:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?', value):
        host = value.split('@', 1)[1].split(':', 1)[0].lower()
    else:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme not in {'https', 'ssh'} or not parsed.hostname:
            return None, 'GIT_URL_INVALID'
        if parsed.username or parsed.password or parsed.port not in {None, 22, 443}:
            return None, 'GIT_URL_INVALID'
        host = parsed.hostname.lower()
    expected_hosts = {
        'github': {'github.com'},
        'gitlab': {'gitlab.com'},
        'bitbucket': {'bitbucket.org'},
    }
    if provider in expected_hosts and host not in expected_hosts[provider]:
        return None, 'GIT_PROVIDER_MISMATCH'
    return value, None


def validate_git_branch(branch):
    value = (branch or 'main').strip()
    if not GIT_BRANCH_PATTERN.fullmatch(value) or '..' in value or value.endswith(('.', '/')) or '@{' in value:
        return None
    return value


def git_error_code(output):
    lowered = (output or '').lower()
    if 'authentication failed' in lowered or 'permission denied' in lowered or 'could not read username' in lowered:
        return 'GIT_AUTH_FAILED'
    if 'repository not found' in lowered or 'not found' in lowered:
        return 'GIT_REPOSITORY_NOT_FOUND'
    if 'could not resolve host' in lowered or 'failed to connect' in lowered or 'timed out' in lowered:
        return 'GIT_NETWORK_ERROR'
    return 'GIT_CONNECTION_FAILED'


def test_git_connection(repo_url, branch='main', provider='generic', auth_type='public', token='', ssh_private_key=''):
    validated_url, validation_error = validate_git_repository_url(repo_url, provider=provider)
    validated_branch = validate_git_branch(branch)
    if validation_error:
        return {'success': False, 'code': validation_error}
    if not validated_branch:
        return {'success': False, 'code': 'GIT_BRANCH_INVALID'}
    if auth_type not in GIT_AUTH_TYPES:
        return {'success': False, 'code': 'GIT_AUTH_TYPE_INVALID'}
    command_url = build_authenticated_repo_url(validated_url, git_token=token if auth_type == 'pat' else None)
    env = os.environ.copy()
    env['GIT_TERMINAL_PROMPT'] = '0'
    temporary_key = None
    try:
        if auth_type == 'ssh':
            if not ssh_private_key or len(ssh_private_key) > 65536 or 'PRIVATE KEY' not in ssh_private_key:
                return {'success': False, 'code': 'GIT_SSH_KEY_INVALID'}
            descriptor, temporary_key = tempfile.mkstemp(prefix='git-key-', dir=app.instance_path)
            os.write(descriptor, ssh_private_key.encode('utf-8'))
            os.close(descriptor)
            os.chmod(temporary_key, 0o600)
            env['GIT_SSH_COMMAND'] = f'ssh -i {temporary_key} -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=accept-new'
        process = subprocess.run(
            ['git', 'ls-remote', '--exit-code', '--heads', command_url, f'refs/heads/{validated_branch}'],
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
            check=False,
        )
        output = sanitize_git_error_output((process.stdout or '') + '\n' + (process.stderr or ''), token)
        if process.returncode == 0 and process.stdout.strip():
            commit = process.stdout.split()[0]
            return {'success': True, 'code': 'GIT_CONNECTED', 'commit': commit[:40]}
        if process.returncode == 2 and not output.strip():
            return {'success': False, 'code': 'GIT_BRANCH_NOT_FOUND'}
        if not process.stdout.strip() and process.returncode:
            code = git_error_code(output)
            if code == 'GIT_CONNECTION_FAILED' and 'remote:' not in output.lower():
                code = 'GIT_BRANCH_NOT_FOUND'
            return {'success': False, 'code': code}
        return {'success': False, 'code': git_error_code(output)}
    except subprocess.TimeoutExpired:
        return {'success': False, 'code': 'GIT_NETWORK_TIMEOUT'}
    except OSError:
        return {'success': False, 'code': 'GIT_CLIENT_UNAVAILABLE'}
    finally:
        if temporary_key:
            try:
                os.unlink(temporary_key)
            except OSError:
                pass


def write_integration_secret(integration, secret_payload):
    os.makedirs(INTEGRATION_SECRETS_ROOT, mode=0o700, exist_ok=True)
    os.chmod(INTEGRATION_SECRETS_ROOT, 0o700)
    path = os.path.join(INTEGRATION_SECRETS_ROOT, f'{integration.id}.json')
    temporary_path = path + '.tmp'
    with open(temporary_path, 'w', encoding='utf-8') as handle:
        json.dump(secret_payload, handle)
    os.chmod(temporary_path, 0o600)
    os.replace(temporary_path, path)
    integration.secret_ref = path
    return path


def read_integration_secret(integration):
    if not integration or not integration.secret_ref:
        return {}
    expected_root = os.path.realpath(INTEGRATION_SECRETS_ROOT)
    path = os.path.realpath(integration.secret_ref)
    if os.path.commonpath([expected_root, path]) != expected_root:
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            value = json.load(handle)
            return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_application_secret(site_id, category, item_id, value):
    directory = os.path.join(APPLICATION_SECRETS_ROOT, str(int(site_id)), category)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    path = os.path.join(directory, f'{int(item_id)}.secret')
    temporary = path + '.tmp'
    with open(temporary, 'w', encoding='utf-8') as handle:
        handle.write(value)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return path


def read_application_secret(path, site_id):
    expected = os.path.realpath(os.path.join(APPLICATION_SECRETS_ROOT, str(int(site_id))))
    candidate = os.path.realpath(path or '')
    if not candidate or os.path.commonpath([expected, candidate]) != expected:
        return ''
    try:
        with open(candidate, 'r', encoding='utf-8') as handle:
            return handle.read()
    except OSError:
        return ''


def sync_runtime_environment(site):
    access = ensure_application_access(site)
    if not access.deployment_root:
        return None
    rows = EnvironmentVariable.query.filter_by(application_id=site.id).order_by(EnvironmentVariable.key).all()
    env_path = os.path.join(access.deployment_root, 'env.list')
    os.makedirs(access.deployment_root, exist_ok=True)
    with open(env_path + '.tmp', 'w', encoding='utf-8') as handle:
        for row in rows:
            value = read_application_secret(row.secret_ref, site.id).replace('$', '$$').replace('\n', '\\n')
            handle.write(f'{row.key}={value}\n')
    os.chmod(env_path + '.tmp', 0o600)
    os.replace(env_path + '.tmp', env_path)
    return env_path


def application_runtime_metrics(access):
    metadata_path = os.path.join(access.deployment_root or '', 'runtime.json')
    if not os.path.isfile(metadata_path): return {}
    try: metadata = json.load(open(metadata_path, encoding='utf-8'))
    except (OSError, json.JSONDecodeError): return {}
    process = subprocess.run(
        ['docker', 'stats', '--no-stream', '--format', '{{json .}}'], capture_output=True, text=True, timeout=15, check=False,
    )
    rows = []
    for line in (process.stdout or '').splitlines():
        try: item = json.loads(line)
        except json.JSONDecodeError: continue
        if str(item.get('Name', '')).startswith(metadata.get('project', '') + '-'):
            rows.append({'name':item.get('Name'),'cpu':item.get('CPUPerc'),'memory':item.get('MemUsage'),'pids':item.get('PIDs')})
    return {'containers': rows, 'limits': {'cpu':'0.75–1.0 cores','memory':'256–512 MB','pids':'128–192'}}


def set_environment_value(site, user, key, value):
    row = EnvironmentVariable.query.filter_by(application_id=site.id, key=key).first()
    if not row:
        row = EnvironmentVariable(application_id=site.id, key=key, secret_ref='pending', created_by=user.id)
        db.session.add(row)
        db.session.flush()
    row.secret_ref = write_application_secret(site.id, 'env', row.id, value)
    return row


def mysql_provision_connection():
    return pymysql.connect(
        read_default_file=MYSQL_PROVISION_CNF, database='myh_admin',
        ssl={'check_hostname': False},
        connect_timeout=5, read_timeout=30, write_timeout=30, autocommit=True,
    )


def generated_database_identifiers(owner_id, display_name):
    slug = re.sub(r'[^a-z0-9]+', '_', (display_name or '').strip().lower()).strip('_')[:32]
    if not slug:
        raise ValueError('Database name must contain a letter or number.')
    suffix = secrets.token_hex(4)
    return f'myh_{int(owner_id)}_{slug}_{suffix}'[:64], f'u{int(owner_id)}_{secrets.token_hex(6)}'[:32]


def provision_mysql_database(database_name, database_user, password):
    with mysql_provision_connection() as connection:
        with connection.cursor() as cursor:
            cursor.callproc('create_tenant_database', (database_name, database_user, password))


def deprovision_mysql_database(database_name, database_user):
    with mysql_provision_connection() as connection:
        with connection.cursor() as cursor:
            cursor.callproc('drop_tenant_database', (database_name, database_user))
    return True


def reset_mysql_password(database_user, password):
    with mysql_provision_connection() as connection:
        with connection.cursor() as cursor:
            cursor.callproc('reset_tenant_password', (database_user, password))


def database_owner_or_404(resource_id, user):
    resource = db.session.get(DatabaseResource, resource_id)
    if not resource or not resource.application:
        abort(404)
    if not (user.is_admin or resource.application.user_id == user.id):
        abort(404)
    return resource


def mysql_tenant_connection(resource, dictionary=False):
    if resource.engine != 'mysql':
        raise RuntimeError('Database engine is not available in Studio.')
    password = read_application_secret(resource.secret_ref, resource.application_id)
    if not password:
        raise RuntimeError('Database credential is unavailable.')
    return pymysql.connect(
        host=resource.host, port=resource.port, user=resource.database_user, password=password,
        database=resource.database_name, ssl={'check_hostname': False}, connect_timeout=5,
        read_timeout=8, write_timeout=8, autocommit=False,
        cursorclass=pymysql.cursors.DictCursor if dictionary else pymysql.cursors.Cursor,
    )


def quote_mysql_identifier(value):
    value = str(value or '')
    if not value or len(value) > 64 or '\x00' in value:
        raise ValueError('Invalid SQL identifier.')
    return '`' + value.replace('`', '``') + '`'


def mysql_table_names(resource, connection=None):
    owned = connection is None
    connection = connection or mysql_tenant_connection(resource, dictionary=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT table_name AS name FROM information_schema.tables WHERE table_schema=%s", (resource.database_name,))
            rows = cursor.fetchall()
            return {row['name'] if isinstance(row, dict) else row[0] for row in rows}
    finally:
        if owned: connection.close()


def require_mysql_table(resource, table_name, connection=None):
    if table_name not in mysql_table_names(resource, connection):
        abort(404)
    return table_name


def json_database_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return f'<binary {len(value)} bytes>'
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return str(value)


DATABASE_COLUMN_FIELDS = ('column_name', 'column_type', 'is_nullable', 'column_default', 'column_key', 'extra')
DATABASE_INDEX_FIELDS = ('index_name', 'non_unique', 'columns_list')
DATABASE_FOREIGN_KEY_FIELDS = ('constraint_name', 'column_name', 'referenced_table_name', 'referenced_column_name')


def normalize_database_metadata(rows, fields):
    """Normalize INFORMATION_SCHEMA driver casing into Database Studio's canonical schema."""
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError('Database metadata did not use a named-column cursor.')
        by_name = {str(key).lower(): value for key, value in row.items()}
        normalized.append({field: json_database_value(by_name.get(field)) for field in fields})
    return normalized


def mysql_table_structure(resource, table_name, connection=None):
    owned = connection is None
    connection = connection or mysql_tenant_connection(resource, dictionary=True)
    try:
        require_mysql_table(resource, table_name, connection)
        with connection.cursor() as cursor:
            cursor.execute("""SELECT column_name AS column_name,column_type AS column_type,is_nullable AS is_nullable,
                              column_default AS column_default,column_key AS column_key,extra AS extra
                              FROM information_schema.columns WHERE table_schema=%s AND table_name=%s
                              ORDER BY ordinal_position""", (resource.database_name, table_name))
            columns = normalize_database_metadata(cursor.fetchall(), DATABASE_COLUMN_FIELDS)
            cursor.execute("""SELECT index_name AS index_name,non_unique AS non_unique,
                              GROUP_CONCAT(column_name ORDER BY seq_in_index) AS columns_list
                              FROM information_schema.statistics WHERE table_schema=%s AND table_name=%s
                              GROUP BY index_name,non_unique ORDER BY index_name""", (resource.database_name, table_name))
            indexes = normalize_database_metadata(cursor.fetchall(), DATABASE_INDEX_FIELDS)
            cursor.execute("""SELECT constraint_name AS constraint_name,column_name AS column_name,
                              referenced_table_name AS referenced_table_name,referenced_column_name AS referenced_column_name
                              FROM information_schema.key_column_usage
                              WHERE table_schema=%s AND table_name=%s AND referenced_table_name IS NOT NULL""",
                           (resource.database_name, table_name))
            foreign_keys = normalize_database_metadata(cursor.fetchall(), DATABASE_FOREIGN_KEY_FIELDS)
        return {'columns': columns, 'indexes': indexes, 'foreignKeys': foreign_keys}
    finally:
        if owned: connection.close()


STUDIO_COLUMN_TYPE = re.compile(r'^(?:TINYINT|SMALLINT|MEDIUMINT|INT|BIGINT|DECIMAL\([0-9]{1,2},[0-9]{1,2}\)|VARCHAR\([1-9][0-9]{0,3}\)|CHAR\([1-9][0-9]{0,2}\)|TEXT|MEDIUMTEXT|LONGTEXT|DATE|DATETIME|TIMESTAMP|BOOLEAN|JSON|BLOB)$', re.I)
STUDIO_BLOCKED_SQL = re.compile(r'\b(?:USE|GRANT|REVOKE|CREATE\s+USER|ALTER\s+USER|DROP\s+USER|CREATE\s+DATABASE|DROP\s+DATABASE|SET\s+GLOBAL|SHUTDOWN|KILL|LOAD\s+DATA|INTO\s+OUTFILE|INTO\s+DUMPFILE)\b', re.I)
STUDIO_DESTRUCTIVE_SQL = re.compile(r'^\s*(?:DROP\s+TABLE|TRUNCATE\s+TABLE|ALTER\s+TABLE\b.*\bDROP\b)', re.I | re.S)


def database_size_bytes(resource):
    password = read_application_secret(resource.secret_ref, resource.application_id)
    if not password:
        return 0
    connection = pymysql.connect(host=resource.host, port=resource.port, user=resource.database_user,
                                 password=password, database=resource.database_name,
                                 ssl={'check_hostname': False}, connect_timeout=5)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COALESCE(SUM(data_length + index_length), 0) FROM information_schema.tables WHERE table_schema=%s", (resource.database_name,))
            return int(cursor.fetchone()[0] or 0)
    finally:
        connection.close()


def database_backup_directory(resource):
    path = os.path.join(MYSQL_BACKUP_ROOT, str(resource.application.user_id), str(resource.id))
    os.makedirs(path, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def database_client_defaults(resource):
    handle = tempfile.NamedTemporaryFile(mode='w', prefix='myh-mysql-', delete=False)
    try:
        os.chmod(handle.name, 0o600)
        password = read_application_secret(resource.secret_ref, resource.application_id)
        handle.write('[client]\n')
        handle.write(f'host={resource.host}\nport={resource.port}\nuser={resource.database_user}\npassword={password}\nssl-mode=REQUIRED\n')
        handle.close()
        return handle.name
    except Exception:
        handle.close()
        os.unlink(handle.name)
        raise


def create_database_backup(resource):
    destination = os.path.join(database_backup_directory(resource), datetime.now().strftime('%Y%m%d-%H%M%S') + '.sql')
    defaults = database_client_defaults(resource)
    try:
        with open(destination, 'wb') as output:
            process = subprocess.run(['mysqldump', f'--defaults-extra-file={defaults}', '--single-transaction',
                                      '--triggers', '--no-tablespaces', resource.database_name],
                                     stdout=output, stderr=subprocess.PIPE, timeout=300, check=False)
        if process.returncode:
            os.unlink(destination)
            raise RuntimeError('Database backup failed.')
        os.chmod(destination, 0o600)
        digest = hashlib.sha256()
        with open(destination, 'rb') as backup_file:
            for chunk in iter(lambda: backup_file.read(1024 * 1024), b''):
                digest.update(chunk)
        checksum_path = destination + '.sha256'
        with open(checksum_path, 'w', encoding='ascii') as checksum_file:
            checksum_file.write(digest.hexdigest() + '\n')
        os.chmod(checksum_path, 0o600)
        return destination
    finally:
        os.unlink(defaults)


def restore_database_backup(resource, backup_name):
    if not re.fullmatch(r'\d{8}-\d{6}\.sql', backup_name):
        abort(404)
    source = os.path.realpath(os.path.join(database_backup_directory(resource), backup_name))
    if os.path.dirname(source) != os.path.realpath(database_backup_directory(resource)) or not os.path.isfile(source):
        abort(404)
    checksum_path = source + '.sha256'
    if not os.path.isfile(checksum_path):
        raise RuntimeError('Database backup checksum is missing.')
    with open(checksum_path, encoding='ascii') as checksum_file:
        expected_digest = checksum_file.read().strip()
    actual_digest = hashlib.sha256()
    with open(source, 'rb') as backup_file:
        for chunk in iter(lambda: backup_file.read(1024 * 1024), b''):
            actual_digest.update(chunk)
    if not expected_digest or not secrets.compare_digest(expected_digest, actual_digest.hexdigest()):
        raise RuntimeError('Database backup checksum verification failed.')
    defaults = database_client_defaults(resource)
    try:
        with open(source, 'rb') as input_file:
            process = subprocess.run(['mysql', f'--defaults-extra-file={defaults}', resource.database_name],
                                     stdin=input_file, stderr=subprocess.PIPE, timeout=300, check=False)
        if process.returncode:
            raise RuntimeError('Database restore failed.')
    finally:
        os.unlink(defaults)


def wordpress_cli(stack_root, arguments, input_text=None, timeout=300):
    """Run a fixed WP-CLI argv inside the managed application container."""
    allowed_commands = {
        ('core', 'install'), ('core', 'is-installed'), ('core', 'version'), ('core', 'update'), ('option', 'get'), ('option', 'update'),
        ('post', 'create'), ('post', 'get'), ('post', 'delete'), ('plugin', 'install'), ('plugin', 'activate'),
        ('plugin', 'deactivate'), ('plugin', 'delete'), ('theme', 'install'), ('theme', 'activate'),
        ('media', 'import'), ('rewrite', 'structure'), ('rewrite', 'flush'),
        ('cron', 'event'),
    }
    arguments = [str(item) for item in arguments]
    if tuple(arguments[:2]) not in allowed_commands or any('\x00' in item or '\n' in item for item in arguments):
        raise ValueError('Unsupported WordPress management command.')
    command = ['docker', 'compose', '--project-directory', stack_root, '--profile', 'cli', 'run', '--rm',
               '--no-deps', 'wordpress-cli', *arguments]
    command.insert(command.index('wordpress-cli') + 1, 'wp')
    result = subprocess.run(command, input=input_text, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError('WordPress management command failed.')
    return (result.stdout or '').strip()


def install_wordpress_one_click(site, access, title, admin_username, admin_email, admin_password, language):
    if not re.fullmatch(r'[A-Za-z0-9_.@-]{3,60}', admin_username):
        raise ValueError('WordPress admin username has an invalid format.')
    if not valid_notification_email(admin_email):
        raise ValueError('WordPress admin email is invalid.')
    if len(admin_password) < 16 or len(admin_password) > 200:
        raise ValueError('WordPress admin password must contain at least 16 characters.')
    if language not in {'uk', 'en_US'}:
        raise ValueError('Unsupported WordPress language.')
    public_url = f"https://{site.custom_domain or site.name + '.' + CLOUDFLARE_ZONE_NAME}"
    arguments = ['core', 'install', f'--url={public_url}', f'--title={title[:120]}',
                 f'--admin_user={admin_username}', f'--admin_email={admin_email}', f'--locale={language}',
                 '--skip-email', '--prompt=admin_password']
    wordpress_cli(access.deployment_root, arguments, input_text=admin_password + '\n')
    wordpress_cli(access.deployment_root, ['rewrite', 'structure', '/%postname%/', '--hard'])
    wordpress_cli(access.deployment_root, ['rewrite', 'flush', '--hard'])
    return public_url


def detect_wordpress_state(site, access):
    root = application_root(access, bucket='file')
    required = ('wp-admin', 'wp-content', 'wp-includes', 'wp-settings.php')
    files_present = all(os.path.exists(os.path.join(root, item)) for item in required)
    config_present = os.path.isfile(os.path.join(root, 'wp-config.php'))
    nested = os.path.isdir(os.path.join(root, 'wordpress', 'wp-admin'))
    resource = DatabaseResource.query.filter_by(application_id=site.id, engine='mysql').first()
    if not files_present:
        return {'code': 'files_missing', 'files': False, 'config': config_present, 'database': bool(resource), 'nested': nested}
    if not config_present:
        return {'code': 'configuration_required', 'files': True, 'config': False, 'database': bool(resource), 'nested': False}
    installed = False
    if resource:
        try:
            with open(os.path.join(root, 'wp-config.php'), encoding='utf-8', errors='ignore') as config_file:
                prefix_match = re.search(r"\$table_prefix\s*=\s*['\"]([A-Za-z0-9_]{1,32})['\"]", config_file.read(256 * 1024))
            table_prefix = prefix_match.group(1) if prefix_match else 'wp_'
            connection = mysql_tenant_connection(resource)
            try:
                with connection.cursor() as cursor:
                    cursor.execute(f'SELECT option_value FROM {quote_mysql_identifier(table_prefix + "options")} WHERE option_name=%s LIMIT 1', ('siteurl',))
                    installed = bool(cursor.fetchone())
            finally:
                connection.close()
        except (OSError, RuntimeError, pymysql.MySQLError):
            return {'code': 'database_error', 'files': True, 'config': True, 'database': True, 'nested': False}
    return {'code': 'installed' if installed else 'installation_required', 'files': True, 'config': True,
            'database': bool(resource), 'nested': False}


def wordpress_config_contents(resource, table_prefix='wp_'):
    if not re.fullmatch(r'[A-Za-z0-9_]{1,32}', table_prefix):
        raise ValueError('Invalid WordPress table prefix.')
    password = read_application_secret(resource.secret_ref, resource.application_id)
    if not password:
        raise RuntimeError('Database credential is unavailable.')
    def php_string(value):
        return str(value).replace('\\', '\\\\').replace("'", "\\'")
    salts = '\n'.join(
        f"define('{name}', '{secrets.token_urlsafe(48)}');" for name in
        ('AUTH_KEY', 'SECURE_AUTH_KEY', 'LOGGED_IN_KEY', 'NONCE_KEY', 'AUTH_SALT', 'SECURE_AUTH_SALT', 'LOGGED_IN_SALT', 'NONCE_SALT')
    )
    return f"""<?php
define('DB_NAME', '{php_string(resource.database_name)}');
define('DB_USER', '{php_string(resource.database_user)}');
define('DB_PASSWORD', '{php_string(password)}');
define('DB_HOST', '{php_string(resource.host)}:{int(resource.port)}');
define('DB_CHARSET', 'utf8mb4');
define('DB_COLLATE', '');
{salts}
$table_prefix = '{table_prefix}';
define('DISALLOW_FILE_EDIT', true);
define('DISABLE_WP_CRON', true);
if (!empty($_SERVER['HTTP_X_FORWARDED_PROTO']) && $_SERVER['HTTP_X_FORWARDED_PROTO'] === 'https') {{ $_SERVER['HTTPS'] = 'on'; }}
if (!defined('ABSPATH')) {{ define('ABSPATH', __DIR__ . '/'); }}
require_once ABSPATH . 'wp-settings.php';
"""


def deploy_from_git(repo_url, site_path, access=None, git_token=None):
    deploy_tmp_root = os.path.join(app.instance_path, 'deploy_tmp')
    os.makedirs(deploy_tmp_root, exist_ok=True)
    repo_base = secure_filename(os.path.basename(repo_url).split('.')[0]) or 'repo'
    repo_dir = tempfile.mkdtemp(prefix=f'{repo_base}-', dir=deploy_tmp_root)
    clone_url = build_authenticated_repo_url(repo_url, git_token=git_token)
    try:
        subprocess.run(['git', 'clone', '--depth', '1', clone_url, repo_dir], check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if os.path.isdir(os.path.join(repo_dir, 'public')):
            source_dir = os.path.join(repo_dir, 'public')
        elif os.path.isdir(os.path.join(repo_dir, 'dist')):
            source_dir = os.path.join(repo_dir, 'dist')
        else:
            source_dir = repo_dir
        if access:
            enforce_application_quota(access, directory_size_safe(source_dir))
        for root, _, filenames in os.walk(source_dir):
            for filename in filenames:
                src_path = os.path.join(root, filename)
                rel_path = os.path.relpath(src_path, source_dir)
                dst_path = os.path.join(site_path, rel_path)
                os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                with open(src_path, 'rb') as src_handle, open(dst_path, 'wb') as dst_handle:
                    dst_handle.write(src_handle.read())
    finally:
        # Cleanup should never break deployment flow if temp ownership is inconsistent.
        shutil.rmtree(repo_dir, ignore_errors=True)


def activate_staged_deployment(site, access, staged_source):
    site_path = application_root(access, bucket='file')
    detected = detect_stack(staged_source)
    candidates = set(detected.get('candidates') or [])
    normalized = {'docker-compose': 'docker'}
    candidates = {normalized.get(item, item) for item in candidates}
    if site.application_type == 'wordpress' and ('wordpress' in candidates or 'php' in candidates):
        candidates.add('wordpress')
    if detected.get('ambiguous') and site.runtime_type not in candidates:
        raise ValueError(f'Ambiguous stack: {", ".join(sorted(candidates))}')
    if candidates and site.runtime_type not in candidates and not (site.runtime_type == 'static' and 'node' in candidates):
        raise ValueError(f'Detected stack {", ".join(sorted(candidates))} does not match runtime {site.runtime_type}')
    enforce_application_quota(access, directory_size_safe(staged_source))
    parent = os.path.dirname(site_path)
    os.makedirs(parent, exist_ok=True)
    candidate = os.path.join(parent, f'.deploy-{site.id}-{secrets.token_hex(6)}')
    previous = os.path.join(parent, f'.previous-{site.id}-{datetime.now().strftime("%Y%m%d%H%M%S")}')
    shutil.copytree(staged_source, candidate)
    had_previous = os.path.isdir(site_path)
    old_metadata = {}
    metadata_path = os.path.join(access.deployment_root, 'runtime.json')
    if os.path.isfile(metadata_path):
        try:
            old_metadata = json.loads(open(metadata_path, encoding='utf-8').read())
        except (OSError, json.JSONDecodeError):
            old_metadata = {}
    try:
        if had_previous:
            os.replace(site_path, previous)
        os.replace(candidate, site_path)
        inferred = infer_runtime_commands(site_path, site.runtime_type)
        install_command = site.install_command or inferred['install_command']
        build_command = site.build_command or inferred['build_command']
        start_command = site.start_command or inferred['start_command']
        if site.application_type == 'wordpress':
            prepare_wordpress_runtime(access.deployment_root, site_path, site.internal_port)
        elif site.runtime_type == 'docker' and ({'docker', 'docker-compose'} & set(detected.get('candidates') or [])):
            prepare_custom_docker(access.deployment_root, site_path, site.internal_port)
        else:
            prepare_runtime(access.deployment_root, site_path, site.runtime_type, site.runtime_version, site.internal_port,
                            install_command=install_command, build_command=build_command, start_command=start_command,
                            spa_enabled=site.spa_enabled)
        code, output = compose_action(access.deployment_root, 'start')
        metadata = json.loads(open(os.path.join(access.deployment_root, 'runtime.json'), encoding='utf-8').read())
        checked = healthcheck(metadata, timeout=25) if code == 0 else {'ok': False}
        if not checked.get('ok'):
            raise RuntimeError(f'health check failed: {output[-500:]}')
        if os.path.isdir(previous): remove_tree(previous)
        site.deployment_status = 'success'; site.runtime_status = 'running'; site.last_restart_at = datetime.now(); db.session.commit()
        return detected
    except Exception:
        failed = site_path + '.failed'
        if os.path.exists(site_path): os.replace(site_path, failed)
        if had_previous and os.path.exists(previous): os.replace(previous, site_path)
        if os.path.isdir(failed): remove_tree(failed)
        if os.path.isdir(candidate): remove_tree(candidate)
        if had_previous:
            old_detected = detect_stack(site_path)
            if site.application_type == 'wordpress':
                prepare_wordpress_runtime(access.deployment_root, site_path, site.internal_port)
            elif site.runtime_type == 'docker' and ({'docker', 'docker-compose'} & set(old_detected.get('candidates') or [])):
                prepare_custom_docker(access.deployment_root, site_path, site.internal_port)
            else:
                prepare_runtime(access.deployment_root, site_path, site.runtime_type, site.runtime_version, site.internal_port,
                                install_command=old_metadata.get('install_command'), build_command=old_metadata.get('build_command'),
                                start_command=old_metadata.get('start_command'), spa_enabled=site.spa_enabled)
            compose_action(access.deployment_root, 'start')
        site.deployment_status = 'failed'; db.session.commit()
        raise


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


def sqlite_application_health():
    database_path = os.path.join(app.instance_path, 'hosting.db')
    result = {'engine': 'SQLite', 'status': 'failed', 'size_bytes': 0, 'journal_mode': None,
              'busy_timeout_ms': None, 'foreign_keys': None, 'integrity': 'not checked'}
    try:
        connection = sqlite3.connect(database_path, timeout=30)
        result.update(
            status='healthy', size_bytes=os.path.getsize(database_path),
            integrity=connection.execute('PRAGMA integrity_check').fetchone()[0],
            journal_mode=connection.execute('PRAGMA journal_mode').fetchone()[0],
            busy_timeout_ms=connection.execute('PRAGMA busy_timeout').fetchone()[0],
            foreign_keys=bool(connection.execute('PRAGMA foreign_keys').fetchone()[0]),
        )
        connection.close()
        if result['integrity'] != 'ok':
            result['status'] = 'failed'
    except (OSError, sqlite3.Error):
        pass
    return result


def platform_backup_status():
    try:
        with open('/var/lib/myh-backup/status.json', encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {'local_status': 'unknown', 'remote_status': 'not_configured', 'last_restore_test': None}


def admin_platform_status():
    """Return the canonical admin status model consumed by dashboard and API."""
    backup = platform_backup_status()
    notifications = notification_statuses()
    sqlite_health = sqlite_application_health()
    tls = edge_tls_certificate_status(CLOUDFLARE_ZONE_NAME)
    sftp_accounts = system_sftp_inventory(SftpAccount.query.all())
    items = []

    def add(item_id, category, severity, status, title, message, action, action_url, requires_action):
        items.append({
            'id': item_id, 'category': category, 'severity': severity, 'status': status,
            'title': title, 'message': message, 'action': action,
            'action_url': action_url, 'requires_action': bool(requires_action),
        })

    remote_status = str(backup.get('remote_status') or 'not_configured').lower()
    add(
        'offserver-backup', 'backup', 'P0' if remote_status != 'verified' else 'INFO', remote_status,
        'Off-server backup',
        ('Віддалену копію перевірено поза цим сервером.' if remote_status == 'verified'
         else 'Віддалене сховище не налаштоване або не перевірене. Локальна копія не захищає від втрати сервера.'),
        'Відкрити Backup Center', url_for('developer_backup_center'), remote_status != 'verified',
    )

    configured = sum(1 for provider in notifications if provider.get('configured'))
    add(
        'notification-providers', 'notifications', 'P1' if configured == 0 else 'INFO',
        'configured' if configured else 'not_configured', 'Операційні сповіщення',
        f'Налаштовано каналів: {configured} з {len(notifications)}. Telegram та SMTP потребують серверних секретів.',
        'Налаштувати / перевірити', url_for('developer_notifications'), configured == 0,
    )

    pending_sftp = [row for row in sftp_accounts if row['classification'] == 'UNKNOWN']
    add(
        'sftp-classification', 'sftp', 'P1' if pending_sftp else 'INFO',
        'review_required' if pending_sftp else 'classified', 'Класифікація SFTP-акаунтів',
        (f'{len(pending_sftp)} системні акаунти не пов’язані з панеллю та потребують ручного підтвердження. '
         'Автоматичне видалення заборонене.' if pending_sftp else 'Усі системні SFTP-акаунти класифіковано.'),
        'Переглянути інвентар', url_for('developer_sftp_users'), bool(pending_sftp),
    )

    orphan_sites = orphan_site_inventory()
    add(
        'orphan-site-directories', 'storage', 'P1' if orphan_sites else 'INFO',
        'review_required' if orphan_sites else 'classified', 'Legacy site directories',
        (f'{len(orphan_sites)} каталогів не мають Site record; вони збережені без змін і потребують рішення адміністратора.'
         if orphan_sites else 'Усі каталоги сайтів пов’язані з Site records.'),
        'Переглянути JSON-інвентар', url_for('api_admin_orphan_sites'), bool(orphan_sites),
    )

    sqlite_ok = sqlite_health.get('status') == 'healthy' and sqlite_health.get('integrity') == 'ok'
    add(
        'application-database', 'database', 'INFO' if sqlite_ok else 'P0',
        'healthy' if sqlite_ok else 'failed', 'База даних застосунку',
        (f"SQLite працює справно, integrity={sqlite_health.get('integrity')}. Клієнтські MySQL-бази незалежні."
         if sqlite_ok else 'Перевірка цілісності SQLite не пройдена.'),
        'Переглянути системний аудит', url_for('developer_system_audit'), not sqlite_ok,
    )

    tls_ok = tls.get('status') == 'valid' and (tls.get('days_remaining') or 0) >= 30
    add(
        'edge-tls', 'tls', 'INFO' if tls_ok else ('P1' if tls.get('status') == 'expiring' else 'P0'),
        tls.get('status') or 'failed', 'Edge SSL/TLS',
        (f"Сертифікат дійсний, керується Cloudflare; залишилось {tls.get('days_remaining')} днів."
         if tls_ok else 'Edge TLS недійсний або потребує швидкого оновлення.'),
        'Переглянути DNS і SSL', url_for('developer_dns_ssl'), not tls_ok,
    )
    return {
        'items': items,
        'attention_items': [item for item in items if item['requires_action']],
        'platform_status': [item for item in items if not item['requires_action']],
        'generated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    }


def orphan_site_inventory():
    if app.config.get('TESTING'):
        return []
    registered = {os.path.realpath(os.path.join(app.config['UPLOAD_FOLDER'], site.folder_name)) for site in Site.query.all()}
    rows = []
    try:
        entries = list(os.scandir(app.config['UPLOAD_FOLDER']))
    except OSError:
        return rows
    for entry in entries:
        if not entry.is_dir(follow_symlinks=False) or os.path.realpath(entry.path) in registered:
            continue
        root = entry.path
        wordpress = all(os.path.exists(os.path.join(root, name)) for name in ('wp-admin', 'wp-content', 'wp-includes'))
        if not wordpress and os.path.isdir(os.path.join(root, 'public_html')):
            candidate = os.path.join(root, 'public_html')
            wordpress = all(os.path.exists(os.path.join(candidate, name)) for name in ('wp-admin', 'wp-content', 'wp-includes'))
        detected = 'wordpress' if wordpress else ('php' if any(name.endswith('.php') for _, _, names in os.walk(root) for name in names[:20]) else 'static_or_unknown')
        stat = entry.stat(follow_symlinks=False)
        rows.append({'name': entry.name, 'path': root, 'sizeBytes': directory_size_safe(root),
                     'modifiedAt': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                     'detectedType': detected, 'classification': 'RECOVERABLE' if wordpress else 'UNKNOWN'})
    return sorted(rows, key=lambda row: row['name'])


@app.route('/api/admin/orphan-sites')
def api_admin_orphan_sites():
    if 'user_id' not in session:
        abort(401)
    user = db.session.get(User, session['user_id'])
    if not user or (not user.is_admin and user_role(user) != 'admin'):
        abort(403)
    return jsonify({'directories': orphan_site_inventory(), 'destructiveActionsEnabled': False, 'requestId': g.request_id})


def read_text_command(command, timeout=10, max_lines=None):
    code, output = run_command(command, timeout=timeout)
    lines = [line.rstrip() for line in (output or '').splitlines() if line.strip()]
    if max_lines is not None:
        lines = lines[:max_lines]
    return {
        'ok': code == 0,
        'output': output or '',
        'lines': lines,
    }


def read_os_release():
    data = {}
    try:
        with open('/etc/os-release', 'r', encoding='utf-8') as handle:
            for line in handle:
                line = line.strip()
                if not line or '=' not in line:
                    continue
                key, value = line.split('=', 1)
                data[key] = value.strip().strip('"')
    except OSError:
        pass
    return data


def probe_public_ip():
    try:
        request_object = urllib.request.Request(
            'https://ifconfig.me/ip',
            headers={'User-Agent': 'myh-guru-audit/1.0'},
        )
        with urllib.request.urlopen(request_object, timeout=5) as response:
            return response.read().decode('utf-8', errors='ignore').strip()
    except Exception:
        return ''


def build_system_audit():
    hostname = read_text_command(['hostname'], timeout=5)['output'].strip()
    host_ips = read_text_command(['hostname', '-I'], timeout=5)['output'].strip().split()
    uname = read_text_command(['uname', '-a'], timeout=5)['output'].strip()
    uptime = read_text_command(['uptime'], timeout=5)['output'].strip()
    uptime_short = uptime.split(' up ', 1)[1].split(',', 1)[0] if ' up ' in uptime else uptime
    os_release = read_os_release()
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage('/')
    load_avg = os.getloadavg()[0] if hasattr(os, 'getloadavg') else 0
    route_output = read_text_command(['ip', 'route'], timeout=5)['lines']
    default_route = next((line for line in route_output if line.startswith('default ')), '')
    resolved = read_text_command(['resolvectl', 'status'], timeout=8)['lines']
    listeners = read_text_command(['ss', '-tulpn'], timeout=8, max_lines=40)
    ufw_status = read_text_command(['ufw', 'status', 'numbered'], timeout=8)
    docker_networks = read_text_command(['docker', 'network', 'ls', '--format', '{{.Name}}'], timeout=10)['lines']
    docker_volumes = read_text_command(['docker', 'volume', 'ls', '--format', '{{.Name}}'], timeout=10)['lines']
    docker_compose = read_text_command(['docker', 'compose', 'ls'], timeout=10)['lines']
    containers = list_docker_containers()
    restarting = list_restarting_containers()
    failed_services = list_failed_services()
    services = {
        'panel': get_service_status('myh-guru'),
        'tunnel': get_service_status('cloudflared'),
        'ssh': get_service_status('ssh'),
        'docker': get_service_status('docker'),
        'fail2ban': get_service_status('fail2ban'),
        'mysql': get_service_status('mysql'),
    }
    public_ip = probe_public_ip()
    container_by_name = {item['name']: item for item in containers}
    required_images = ('myh-stack-php:8.2', 'myh-stack-php:8.3', 'myh-stack-php:8.4', 'myh-stack-python-web:latest', 'myh-stack-web:latest')
    missing_images = [image for image in required_images if run_command(['docker', 'image', 'inspect', image], timeout=10)[0] != 0]
    nextcloud = container_by_name.get('nextcloud')
    backup_roots = ('/srv/backups/platform-audit', MYSQL_BACKUP_ROOT)
    backups_ready = os.path.isdir(backup_roots[0]) and any(os.scandir(backup_roots[0])) and os.path.isdir(backup_roots[1])
    readiness = [
        {
            'name': 'General Docker Hosting',
            'status': 'READY' if services['docker']['active'] and not missing_images else 'WARNING',
            'detail': 'Docker engine and all managed runtime images are available.' if not missing_images else 'Missing runtime images: ' + ', '.join(missing_images),
        },
        {
            'name': 'Public ingress',
            'status': 'READY' if services['tunnel']['active'] else 'NOT READY',
            'detail': 'Cloudflare Tunnel is the active public ingress.' if services['tunnel']['active'] else services['tunnel']['detail'],
        },
        {
            'name': 'MySQL Hosting',
            'status': 'READY' if services['mysql']['active'] else 'NOT READY',
            'detail': 'MySQL provisioning and isolated application databases are available.' if services['mysql']['active'] else services['mysql']['detail'],
        },
        {
            'name': 'Nextcloud',
            'status': 'READY' if nextcloud and nextcloud['state'] == 'running' and 'healthy' in nextcloud['status'].lower() else 'WARNING',
            'detail': nextcloud['status'] if nextcloud else 'Nextcloud container is not present.',
        },
        {
            'name': 'Backups',
            'status': 'READY' if backups_ready else 'WARNING',
            'detail': 'Platform and MySQL backup repositories are populated.' if backups_ready else 'One or more backup repositories are empty or unavailable.',
        },
        {
            'name': 'Control Panel',
            'status': 'READY' if services['panel']['active'] else 'WARNING',
            'detail': services['panel']['detail'],
        },
    ]
    problems = []
    if restarting:
        problems.append('Restarting containers: ' + ', '.join(restarting[:6]))
    if failed_services:
        problems.append('Failed services: ' + ', '.join(failed_services[:6]))
    if memory.percent >= 90: problems.append(f'RAM usage is critical: {memory.percent:.1f}%.')
    if swap.percent >= 75: problems.append(f'Swap usage is high: {swap.percent:.1f}%.')
    if disk.percent >= 85: problems.append(f'Root filesystem usage is high: {disk.percent:.1f}%.')
    unhealthy = [item['name'] for item in containers if 'unhealthy' in item['status'].lower()]
    if unhealthy: problems.append('Unhealthy containers: ' + ', '.join(unhealthy[:6]))
    if missing_images: problems.append('Missing managed runtime images: ' + ', '.join(missing_images))
    if not public_ip:
        problems.append('Public IP probe unavailable from the panel runtime.')
    return {
        'hostname': hostname or os.uname().nodename,
        'host_ips': host_ips,
        'public_ip': public_ip,
        'uname': uname,
        'os_name': os_release.get('PRETTY_NAME', 'Unknown'),
        'kernel': os_release.get('KERNEL_VERSION', '') or os.uname().release,
        'uptime': uptime_short,
        'load_avg': round(load_avg, 2),
        'cpu_cores': os.cpu_count() or 1,
        'memory_total_gb': round(memory.total / (1024 ** 3), 2),
        'memory_used_gb': round(memory.used / (1024 ** 3), 2),
        'memory_available_gb': round(memory.available / (1024 ** 3), 2),
        'memory_percent': round(memory.percent, 1),
        'swap_total_gb': round(swap.total / (1024 ** 3), 2),
        'swap_used_gb': round(swap.used / (1024 ** 3), 2),
        'swap_percent': round(swap.percent, 1),
        'disk_total_gb': round(disk.total / (1024 ** 3), 2),
        'disk_used_gb': round(disk.used / (1024 ** 3), 2),
        'disk_free_gb': round(disk.free / (1024 ** 3), 2),
        'disk_percent': round(disk.percent, 1),
        'default_route': default_route,
        'resolvectl': resolved[:24],
        'listeners': listeners['lines'],
        'ufw_status': ufw_status['output'].strip() or 'Unavailable',
        'docker_networks': docker_networks,
        'docker_volumes': docker_volumes,
        'docker_compose': docker_compose,
        'containers': containers,
        'restarting': restarting,
        'failed_services': failed_services,
        'services': services,
        'readiness': readiness,
        'problems': problems,
    }


def build_application_registry():
    applications = []
    for site in Site.query.order_by(Site.name).all():
        access = ensure_application_access(site)
        quota = quota_snapshot(access)
        backups = list_site_backups(site)
        domain = site.custom_domain or f'{site.name}.myh.guru'
        status = 'unknown'
        latency_ms = None
        health_error = ''
        try:
            request_object = urllib.request.Request(f'https://{domain}/', method='HEAD', headers={'User-Agent': 'myh-registry/1.0'})
            started = time.monotonic()
            with urllib.request.urlopen(request_object, timeout=5) as response:
                status = 'online' if response.status < 500 else 'degraded'
            latency_ms = round((time.monotonic() - started) * 1000)
        except Exception as exc:
            status = 'offline'
            health_error = str(exc)[:120]
        applications.append({
            'id': site.id,
            'name': site.name,
            'type': {
                'node': 'Node.js', 'python': 'Python', 'php': 'PHP',
                'wordpress': 'WordPress', 'docker': 'Docker', 'static': 'Static',
            }.get(site.runtime_type, (site.runtime_type or 'Unknown').title()),
            'owner': site.owner.username if site.owner else '—',
            'domain': domain,
            'path': access.file_root,
            'upload_root': access.upload_root,
            'deployment_root': access.deployment_root,
            'backup_root': access.backup_root,
            'stack': site.runtime_type or 'unknown',
            'status': status,
            'health_latency_ms': latency_ms,
            'health_error': health_error,
            'backup_count': len(backups),
            'latest_backup': backups[0]['name'] if backups else '—',
            'disk_usage_mb': round(user_usage_bytes(site.owner) / (1024 * 1024), 1) if site.owner else 0,
            'storage_quota_mb': access.storage_quota_mb,
            'backup_quota_mb': access.backup_quota_mb,
            'application_quota_mb': access.application_quota_mb,
            'application_usage_ratio': quota['application_ratio'],
            'backup_usage_ratio': quota['backup_ratio'],
            'wp_permissions_user': parse_json_list(access.wordpress_permissions_user_json, str),
            'wp_permissions_developer': parse_json_list(access.wordpress_permissions_developer_json, str),
        })
    summary = {
        'total': len(applications),
        'online': sum(1 for item in applications if item['status'] == 'online'),
        'offline': sum(1 for item in applications if item['status'] == 'offline'),
        'degraded': sum(1 for item in applications if item['status'] == 'degraded'),
        'backups': sum(item['backup_count'] for item in applications),
    }
    return {'applications': applications, 'summary': summary}


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


def send_notification(message, level='info', alert_key=None, resolved=False):
    service = notification_service()
    return service.send(message, level=level, alert_key=alert_key, resolved=resolved)


def notification_config_store():
    return NotificationConfigStore(os.path.join(app.instance_path, 'notification_config.enc'), panel_secret)


def notification_settings():
    return notification_config_store().load().get('providers', {})


def notification_service():
    return NotificationService.from_env(
        os.path.join(app.instance_path, 'notification_state.json'), stored_settings=notification_settings()
    )


def mask_identifier(value, visible=3):
    value = str(value or '')
    if not value:
        return ''
    if len(value) <= visible * 2:
        return '•' * len(value)
    return value[:visible] + '•' * (len(value) - visible * 2) + value[-visible:]


def notification_statuses():
    stored = notification_settings()
    service = notification_service()
    rows = []
    for provider in service.providers:
        values = stored.get(provider.name) or {}
        enabled = bool(provider.enabled)
        complete = bool(provider.configured or (not enabled and (
            (provider.name == 'telegram' and provider.token and provider.chat_id) or
            (provider.name == 'smtp' and provider.host and provider.sender and provider.recipients)
        )))
        if not complete:
            status = 'NOT_CONFIGURED'
        elif not enabled:
            status = 'DISABLED'
        elif values.get('last_test_ok') is True:
            status = 'CONFIGURED'
        else:
            status = 'ERROR'
        row = {
            'provider': provider.name, 'status': status, 'enabled': enabled,
            'configured': status == 'CONFIGURED', 'credentials_saved': complete,
            'last_test_at': values.get('last_test_at'),
        }
        if provider.name == 'telegram':
            row['chat_id_masked'] = mask_identifier(provider.chat_id)
        else:
            row.update(host=provider.host, port=provider.port, username=provider.username,
                       sender=provider.sender, recipients=', '.join(provider.recipients),
                       encryption=provider.encryption)
        rows.append(row)
    return rows


def collect_health_alerts():
    alerts = []
    failed = list_failed_services()
    restarting = list_restarting_containers()
    if failed:
        alerts.append({'level': 'danger', 'title': 'Failed services', 'detail': ', '.join(failed[:6])})
    if restarting:
        alerts.append({'level': 'warning', 'title': 'Restarting containers', 'detail': ', '.join(restarting[:6])})
    if alerts:
        send_notification('Hosting panel detected: ' + '; '.join(item['title'] for item in alerts), level='warning', alert_key='platform-health')
    else:
        send_notification('Platform health checks recovered.', level='info', alert_key='platform-health', resolved=True)
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
    'wordpress.cron': 'WordPress Cron',
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
    access = ensure_application_access(site)
    backups = list_site_backups(site, access=access)
    for old in backups[limit:]:
        try:
            archive_path = os.path.join(backup_directory(site, access=access), old['name'])
            os.remove(archive_path)
            for sidecar in (archive_path + '.wordpress.sql', archive_path + '.wordpress.sql.sha256'):
                if os.path.isfile(sidecar):
                    os.remove(sidecar)
        except OSError:
            continue


def perform_job(job):
    payload = job_payload_dict(job.payload_json)
    if job.job_type == 'site.backup':
        site = db.session.get(Site, payload.get('site_id'))
        if not site:
            raise ValueError('Сайт не знайдено')
        access = ensure_application_access(site)
        set_job_state(job, progress=10, message=f'Backup {site.name} готується')
        create_backup_archive(site, access=access)
        set_job_state(job, progress=80, message=f'Backup {site.name} створено')
        clean_site_backup_retention(site)
        return {'site': site.name, 'folder_name': site.folder_name, 'backups_kept': 10}

    if job.job_type == 'wordpress.cron':
        site = db.session.get(Site, payload.get('site_id'))
        if not site or site.application_type != 'wordpress' or site.provisioning_phase != 'ready':
            raise ValueError('WordPress site is not ready')
        access = ensure_application_access(site)
        wordpress_cli(access.deployment_root, ['cron', 'event', 'run', '--due-now', '--quiet'], timeout=90)
        return {'site': site.name, 'isolated': True}

    if job.job_type == 'site.restore':
        site = db.session.get(Site, payload.get('site_id'))
        backup_name = payload.get('backup_name', '')
        if not site:
            raise ValueError('Сайт не знайдено')
        archive_path = get_backup_path(site, backup_name)
        access = ensure_application_access(site)
        site_path = application_root(access, bucket='file')
        restore_size = estimate_zip_unpacked_bytes(archive_path)
        enforce_application_quota(access, restore_size)
        validate_wordpress_database_sidecar(site, archive_path)
        set_job_state(job, progress=25, message=f'Restore {site.name} очищається')
        for root, directories, filenames in os.walk(site_path, topdown=False):
            for filename in filenames:
                os.remove(os.path.join(root, filename))
            for directory in directories:
                os.rmdir(os.path.join(root, directory))
        set_job_state(job, progress=55, message=f'Restore {site.name} розпаковується')
        with zipfile.ZipFile(archive_path, 'r') as archive:
            safe_extract_zip(archive, site_path)
        reconcile_wordpress_permissions(site, access)
        restore_wordpress_database_sidecar(site, archive_path)
        return {'site': site.name, 'backup_name': backup_name}

    if job.job_type == 'site.delete':
        site = db.session.get(Site, payload.get('site_id'))
        if not site:
            raise ValueError('Сайт не знайдено')
        access = ensure_application_access(site)
        site_path = application_root(access, bucket='file')
        if os.path.exists(site_path):
            create_backup_archive(site, access=access)
            remove_tree(site_path)
        site_name = site.name
        access_row = ApplicationAccess.query.filter_by(site_id=site.id).first()
        if access_row:
            db.session.delete(access_row)
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


def schedule_wordpress_cron_jobs(now=None):
    if app.config.get('TESTING'):
        return 0
    now = now or datetime.now()
    cutoff = now - timedelta(minutes=14)
    created = 0
    for site in Site.query.filter_by(application_type='wordpress', provisioning_phase='ready').all():
        recent = JobTask.query.filter(JobTask.job_type == 'wordpress.cron', JobTask.target == str(site.id),
                                      JobTask.created_at >= cutoff).first()
        if recent:
            continue
        create_job('wordpress.cron', target=str(site.id), payload={'site_id': site.id}, created_by='scheduler')
        created += 1
    return created


def job_worker_loop():
    with app.app_context():
        next_cron_scan = datetime.min
        while True:
            try:
                if datetime.now() >= next_cron_scan:
                    schedule_wordpress_cron_jobs()
                    next_cron_scan = datetime.now() + timedelta(minutes=1)
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


def current_runtime_catalog(language=None):
    return runtime_catalog(
        RUNTIME_CONFIG_FILE,
        RUNTIME_HEALTH_FILE,
        language=language or resolve_language(),
        testing=bool(app.config.get('TESTING')),
    )


def runtime_inventory():
    try:
        config = json.load(open(RUNTIME_CONFIG_FILE, encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        config = {}
    rows = []
    for item in current_runtime_catalog('en'):
        row = dict(item)
        row.update({
            'runtime': item['id'], 'image': None if item['id'] == 'docker' else item['template'],
            'default': config.get(item['id'], {}).get('default', item['version']) == item['version'],
        })
        rows.append(row)
    return rows, config


def allocate_application_port():
    with APPLICATION_PORT_LOCK:
        used = {row[0] for row in db.session.query(Site.internal_port).filter(Site.internal_port.isnot(None)).all()}
        for port in range(20000, 30000):
            if port in used: continue
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                try: probe.bind(('127.0.0.1', port))
                except OSError: continue
                return port
    raise RuntimeError('No internal application ports available')


# Декоратор для перевірки прав розробника (Крок 2)
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        user = db.session.get(User, session['user_id'])
        if not user or not user.is_admin:
            if request.path.startswith('/api/'):
                return jsonify({'error': translate('api_forbidden')}), 403
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function


def developer_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        user = db.session.get(User, session['user_id'])
        if not user:
            return abort(403)
        if user_role(user) not in {'developer', 'admin'} and not user.is_admin:
            return abort(403)
        return f(*args, **kwargs)
    return decorated_function

# Динамічний перехоплювач піддоменів (враховує блокування сайтів і користувачів)
def resolve_site_request_path(site, site_path, requested_path):
    path = (requested_path or '').lstrip('/')
    if path:
        return path
    default_index = os.path.join(site_path, 'index.html')
    default_php_index = os.path.join(site_path, 'index.php')
    if os.path.exists(default_index):
        return 'index.html'
    if os.path.exists(default_php_index):
        return 'index.php'
    try:
        with os.scandir(site_path) as entries:
            has_existing_content = next(entries, None) is not None
    except OSError:
        has_existing_content = False
    if not has_existing_content:
        try:
            os.makedirs(site_path, exist_ok=True)
            scaffold_site_content(site_path, site.name)
        except OSError:
            return 'index.html'
    return 'index.html'


def is_scaffold_index(index_path):
    if not os.path.isfile(index_path):
        return False
    try:
        with open(index_path, 'r', encoding='utf-8') as handle:
            content = handle.read(4096)
    except OSError:
        return False
    return 'працює</h1><p>Завантажте файли статичного сайту через файловий менеджер.' in content


def detect_nested_site_entry(site_path):
    try:
        names = sorted(os.listdir(site_path))
    except OSError:
        return None
    dirs = [name for name in names if os.path.isdir(os.path.join(site_path, name))]
    if len(dirs) != 1:
        return None
    nested = dirs[0]
    nested_index_html = os.path.join(site_path, nested, 'index.html')
    nested_index_php = os.path.join(site_path, nested, 'index.php')
    if os.path.isfile(nested_index_html) or os.path.isfile(nested_index_php):
        return nested
    return None


def site_public_root(site):
    access = ApplicationAccess.query.filter_by(site_id=site.id).first()
    if access and access.file_root and os.path.isdir(access.file_root):
        return access.file_root
    return os.path.join(app.config['UPLOAD_FOLDER'], site.folder_name)


class PreserveUpstreamRedirects(urllib.request.HTTPRedirectHandler):
    """Return upstream redirects to the browser instead of recursively following public URLs."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def proxy_runtime_request(site):
    if site.runtime_type not in {'php', 'node', 'python', 'docker', 'wordpress'} or not site.internal_port:
        return None
    target = f'http://127.0.0.1:{site.internal_port}{request.full_path}'
    if target.endswith('?'):
        target = target[:-1]
    hop_by_hop_headers = {
        'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
        'te', 'trailer', 'trailers', 'transfer-encoding', 'upgrade',
    }
    blocked_headers = hop_by_hop_headers | {'host', 'content-length'}
    headers = {key: value for key, value in request.headers if key.lower() not in blocked_headers}
    headers['X-Forwarded-Host'] = request.host
    headers['X-Forwarded-Proto'] = request.headers.get('X-Forwarded-Proto', request.scheme)
    headers['X-Forwarded-Port'] = '443' if headers['X-Forwarded-Proto'] == 'https' else '80'
    headers['Host'] = request.host
    upstream_request = urllib.request.Request(
        target, data=request.get_data() if request.method not in {'GET', 'HEAD'} else None,
        headers=headers, method=request.method,
    )
    try:
        upstream = urllib.request.build_opener(PreserveUpstreamRedirects).open(upstream_request, timeout=25)
    except urllib.error.HTTPError as exc:
        upstream = exc
    except (urllib.error.URLError, TimeoutError, OSError):
        return Response('Application unavailable', status=503, content_type='text/plain; charset=utf-8')
    response_headers = []
    for key, value in upstream.headers.items():
        if key.lower() not in hop_by_hop_headers | {'content-length'}:
            response_headers.append((key, value))
    return Response(upstream.read(), status=upstream.status, headers=response_headers)


@app.before_request
def handle_subdomain():
    host = request.host.split(':', 1)[0].lower().rstrip('.')
    parts = host.split('.')
    try:
        custom_site = Site.query.filter_by(custom_domain=host).first()
    except OperationalError:
        # If schema is not yet available during bootstrap/recovery, do not block core panel routes.
        custom_site = None
    if custom_site:
        if custom_site.is_banned or (custom_site.owner and custom_site.owner.is_banned):
            abort(403)
        runtime_response = proxy_runtime_request(custom_site)
        if runtime_response is not None:
            return runtime_response
        site_path = site_public_root(custom_site)
        if request.path in {'', '/'}:
            nested_entry = detect_nested_site_entry(site_path)
            if nested_entry and is_scaffold_index(os.path.join(site_path, 'index.html')):
                return redirect(f'/{nested_entry}/')
        path = resolve_site_request_path(custom_site, site_path, request.path)
        if os.path.exists(os.path.join(site_path, path)):
            return send_from_directory(site_path, path)
        return abort(404)

    if len(parts) > 2 and not host.startswith('192.') and not host.startswith('127.'):
        subdomain = parts[0]

        if subdomain not in ['www', 'panel', 'myh']:
            try:
                site = Site.query.filter((Site.name == subdomain) | (Site.folder_name.like(f"%_{subdomain}"))).first()
            except OperationalError:
                site = None
            if site:
                # СУВОРА ПЕРЕВІРКА БЛОКУВАННЯ: перевіряємо сайт і власника
                if site.is_banned or (site.owner and site.owner.is_banned):
                    return "<h1>403 Forbidden</h1><p>Цей сайт або обліковий запис власника заблоковано адміністратором.</p>", 403

                runtime_response = proxy_runtime_request(site)
                if runtime_response is not None:
                    return runtime_response
                site_path = site_public_root(site)
                if request.path in {'', '/'}:
                    nested_entry = detect_nested_site_entry(site_path)
                    if nested_entry and is_scaffold_index(os.path.join(site_path, 'index.html')):
                        return redirect(f'/{nested_entry}/')
                path = resolve_site_request_path(site, site_path, request.path)
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
    runtimes = [public_runtime(item) for item in current_runtime_catalog() if item['available']]
    return render_template('home.html', runtimes=runtimes)


@app.route('/technologies')
def supported_technologies():
    return render_template('technologies.html', runtimes=current_runtime_catalog())


@app.route('/api/runtimes')
def api_runtimes():
    return jsonify({'runtimes': [public_runtime(item) for item in current_runtime_catalog()], 'requestId': g.request_id})


@app.route('/api/runtimes/detect', methods=['POST'])
def api_runtime_detect():
    user = db.session.get(User, session.get('user_id')) if session.get('user_id') else None
    if not user or user.is_banned:
        abort(401)
    require_role_permission(user, 'site.create')
    payload = request.get_json(silent=True) or {}
    files = payload.get('files') or []
    package = payload.get('packageJson') or {}
    if not isinstance(files, list) or len(files) > 500 or not isinstance(package, dict):
        abort(400, 'Invalid project metadata.')
    sanitized = [str(name)[:240] for name in files if isinstance(name, str)]
    detected = detect_file_names(sanitized, package)
    available = {row['id'] for row in current_runtime_catalog() if row['available']}
    detected['available'] = detected.get('recommended') in available
    return jsonify({'detection': detected, 'requestId': g.request_id})


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
        user = User.query.filter((User.username == username) | (func.lower(User.email) == username)).first()
        
        if user and check_password_hash(user.password, password):
            # Перевірка на блокування аккаунта
            if user.is_banned:
                ip_attempts.append(now)
                account_attempts.append(now)
                error = "Невірний логін або пароль!"
            else:
                login_attempts.pop(ip_key, None)
                login_attempts.pop(account_key, None)
                session.clear()
                session.permanent = True
                session['user_id'] = user.id
                session['username'] = user.username
                session['role'] = user_role(user)
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
    fallback = url_for('dashboard' if 'user_id' in session else 'login')
    next_url = sanitize_next_path(request.args.get('next'), fallback)
    return redirect(next_url)


@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404


@app.route('/service-catalog')
def service_catalog():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    if not user:
        session.clear()
        return redirect(url_for('login'))
    if user.is_banned:
        session.clear()
        return redirect(url_for('login'))
    language = get_current_language()
    catalog_items = available_service_catalog(user, language=language)
    return render_template('service_catalog.html', user=user, catalog_items=catalog_items)


@app.route('/service-catalog/<slug>')
def service_catalog_action(slug):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    if not user:
        session.clear()
        return redirect(url_for('login'))
    if user.is_banned:
        session.clear()
        return redirect(url_for('login'))

    item = next((entry for entry in SERVICE_CATALOG if entry['slug'] == slug), None)
    if not item:
        abort(404)
    permission = item.get('permission')
    if permission:
        require_role_permission(user, permission)

    if item.get('site_type'):
        return redirect(url_for('create_site_wizard', site_type=item['site_type']))

    redirect_map = {
        'database': 'developer_applications',
        'connect-git': 'git_integration_wizard',
        'connect-domain': 'developer_dns_ssl' if (user.is_admin or user_role(user) == 'admin') else 'dashboard',
        'create-sftp': 'developer_sftp_access' if user_role(user) in {'developer', 'admin'} or user.is_admin else 'user_sftp_access',
        'connect-smtp': 'developer_notifications',
        'connect-telegram': 'developer_notifications',
        'create-webhook': 'developer_deploy',
        'configure-backups': 'developer_backup_center' if user_role(user) in {'developer', 'admin'} or user.is_admin else 'dashboard',
        'php-health': 'php_health_center',
    }
    endpoint = redirect_map.get(slug)
    if not endpoint:
        abort(404)
    if endpoint == 'dashboard':
        flash('Оберіть сайт у панелі, щоб налаштувати цю дію.', 'info')
    return redirect(url_for(endpoint))


GIT_STATUS_MESSAGES = {
    'uk': {
        'GIT_CONNECTED': 'Репозиторій і гілка доступні.',
        'GIT_URL_INVALID': 'Некоректна або небезпечна URL-адреса репозиторію.',
        'GIT_PROVIDER_MISMATCH': 'URL не відповідає вибраному Git-провайдеру.',
        'GIT_BRANCH_INVALID': 'Некоректна назва гілки.',
        'GIT_BRANCH_NOT_FOUND': 'Вказану гілку не знайдено.',
        'GIT_AUTH_FAILED': 'Не вдалося авторизуватися у Git-провайдера.',
        'GIT_REPOSITORY_NOT_FOUND': 'Репозиторій не знайдено або доступ заборонено.',
        'GIT_NETWORK_ERROR': 'Git-провайдер недоступний через мережеву помилку.',
        'GIT_NETWORK_TIMEOUT': 'Перевірка Git перевищила допустимий час.',
        'GIT_SSH_KEY_INVALID': 'Некоректний SSH private key.',
        'GIT_CLIENT_UNAVAILABLE': 'Git client недоступний на сервері.',
        'GIT_CONNECTION_FAILED': 'Не вдалося перевірити Git-підключення.',
    },
    'en': {
        'GIT_CONNECTED': 'Repository and branch are reachable.',
        'GIT_URL_INVALID': 'The repository URL is invalid or unsafe.',
        'GIT_PROVIDER_MISMATCH': 'The URL does not match the selected Git provider.',
        'GIT_BRANCH_INVALID': 'The branch name is invalid.',
        'GIT_BRANCH_NOT_FOUND': 'The selected branch was not found.',
        'GIT_AUTH_FAILED': 'Authentication with the Git provider failed.',
        'GIT_REPOSITORY_NOT_FOUND': 'Repository was not found or access was denied.',
        'GIT_NETWORK_ERROR': 'The Git provider is unavailable due to a network error.',
        'GIT_NETWORK_TIMEOUT': 'The Git connection check timed out.',
        'GIT_SSH_KEY_INVALID': 'The SSH private key is invalid.',
        'GIT_CLIENT_UNAVAILABLE': 'Git client is unavailable on the server.',
        'GIT_CONNECTION_FAILED': 'The Git connection could not be verified.',
    },
}


def git_status_message(code, language=None):
    selected = 'en' if resolve_language(language) == 'en' else 'uk'
    return GIT_STATUS_MESSAGES[selected].get(code, GIT_STATUS_MESSAGES[selected]['GIT_CONNECTION_FAILED'])


@app.route('/service-catalog/git', methods=['GET', 'POST'])
def git_integration_wizard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    if not user or user.is_banned:
        session.clear()
        return redirect(url_for('login'))
    require_role_permission(user, 'git.connect')
    allowed_ids = assigned_application_ids(user)
    sites = Site.query.filter(Site.id.in_(allowed_ids)).order_by(Site.name.asc()).all() if allowed_ids else []
    integrations = Integration.query.filter(
        Integration.integration_type == 'git',
        Integration.application_id.in_(allowed_ids),
    ).order_by(Integration.updated_at.desc()).all() if allowed_ids else []

    if request.method == 'POST':
        payload = request.get_json(silent=True) if request.is_json else request.form
        payload = payload or {}
        action = str(payload.get('action') or 'test').strip().lower()
        site_id = int(payload.get('application_id') or 0)
        site = db.session.get(Site, site_id) if site_id else None
        if not site or site.id not in allowed_ids:
            return jsonify({'error': {'code': 'RESOURCE_FORBIDDEN', 'message': 'Application is not available.', 'requestId': g.request_id}}), 403
        require_application_permission(user, site, 'git.connect')
        integration = Integration.query.filter_by(application_id=site.id, integration_type='git').first()

        if action == 'disconnect':
            if integration:
                secret_path = integration.secret_ref
                db.session.delete(integration)
                db.session.commit()
                if secret_path:
                    try:
                        os.unlink(secret_path)
                    except OSError:
                        pass
            log_action('integration.git.disconnect', site.name)
            return jsonify({'success': True, 'status': 'disconnected'})

        provider = str(payload.get('provider') or 'github').strip().lower()
        auth_type = str(payload.get('auth_type') or 'public').strip().lower()
        repo_url = str(payload.get('repository_url') or '').strip()
        branch = str(payload.get('branch') or 'main').strip()
        token = str(payload.get('token') or '').strip()
        ssh_private_key = str(payload.get('ssh_private_key') or '')
        if provider not in GIT_PROVIDERS or auth_type not in GIT_AUTH_TYPES:
            result = {'success': False, 'code': 'GIT_CONNECTION_FAILED'}
        else:
            result = test_git_connection(repo_url, branch, provider, auth_type, token, ssh_private_key)
        result['message'] = git_status_message(result['code'])
        result['requestId'] = g.request_id
        if not result['success']:
            log_action('integration.git.test.failed', f'{site.name}:{result["code"]}')
            return jsonify({'error': result}), 422
        log_action('integration.git.test', f'{site.name}:{provider}')
        if action == 'test':
            return jsonify(result)
        if action != 'save':
            return jsonify({'error': {'code': 'ACTION_INVALID', 'message': 'Unsupported action.', 'requestId': g.request_id}}), 400

        if not integration:
            integration = Integration(application_id=site.id, integration_type='git', created_by=user.id)
            db.session.add(integration)
            db.session.flush()
        integration.provider = provider
        integration.status = 'ready'
        integration.config_json = json.dumps({
            'repository_url': repo_url,
            'branch': branch,
            'auth_type': auth_type,
            'last_commit': result.get('commit', ''),
        })
        integration.last_test_at = datetime.now()
        integration.last_test_status = 'success'
        if auth_type == 'pat' and token:
            write_integration_secret(integration, {'token': token})
        elif auth_type == 'ssh' and ssh_private_key:
            write_integration_secret(integration, {'ssh_private_key': ssh_private_key})
        elif auth_type == 'public' and integration.secret_ref:
            try:
                os.unlink(integration.secret_ref)
            except OSError:
                pass
            integration.secret_ref = None
        db.session.commit()
        log_action('integration.git.connect', f'{site.name}:{provider}')
        return jsonify({'success': True, 'status': 'ready', 'integrationId': integration.id, 'message': result['message']})

    rows = []
    for integration in integrations:
        try:
            config = json.loads(integration.config_json or '{}')
        except json.JSONDecodeError:
            config = {}
        rows.append({'integration': integration, 'config': config, 'has_secret': bool(integration.secret_ref)})
    return render_template('git_integration_wizard.html', user=user, sites=sites, git_integrations=rows, providers=sorted(GIT_PROVIDERS))


@app.route('/php-health-center')
def php_health_center():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    if not user:
        session.clear()
        return redirect(url_for('login'))
    if user.is_banned:
        session.clear()
        return redirect(url_for('login'))

    if not user_has_role_permission(user, 'health.view'):
        flash('У вас немає доступу до health-check.', 'error')
        return redirect(url_for('dashboard'))

    site_ids = assigned_application_ids(user)
    if not site_ids:
        return render_template('php_health_center.html', php_sites=[])

    php_sites = Site.query.filter(Site.id.in_(site_ids), Site.php_version == 'php').order_by(Site.name.asc()).all()
    return render_template('php_health_center.html', php_sites=php_sites)


@app.route('/sites/create', methods=['GET', 'POST'])
def create_site_wizard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    if not user:
        session.clear()
        return redirect(url_for('login'))
    if user.is_banned:
        session.clear()
        return redirect(url_for('login'))

    if not user_has_role_permission(user, 'site.create'):
        flash('У вас немає дозволу на створення сайтів.', 'error')
        return redirect(url_for('dashboard'))

    runtime_options = current_runtime_catalog()
    allowed_types = [item['id'] for item in runtime_options if item['available']]
    allowed_php_versions = ['8.2']
    source_modes = ['upload', 'git', 'sftp']
    selected_type = request.values.get('site_type', 'static')
    selected_source = request.values.get('source_mode', 'upload')
    selected_php_version = request.values.get('php_runtime', '8.2')
    if selected_type not in allowed_types:
        selected_type = allowed_types[0] if allowed_types else ''
    if selected_source not in source_modes:
        selected_source = 'upload'
    if selected_php_version not in allowed_php_versions:
        selected_php_version = '8.2'

    if request.method == 'POST':
        subdomain = request.form.get('site_name', '').strip().lower()
        domain = request.form.get('domain', '').strip().lower().rstrip('.')
        site_type = request.form.get('site_type', 'static').strip().lower()
        source_mode = request.form.get('source_mode', 'upload').strip().lower()
        php_runtime = request.form.get('php_runtime', '8.2').strip()
        install_command = (request.form.get('install_command') or '').strip()
        build_command = (request.form.get('build_command') or '').strip()
        start_command = (request.form.get('start_command') or '').strip()
        spa_enabled = site_type == 'static' and request.form.get('spa_enabled') == '1'
        wordpress_mode = (request.form.get('wordpress_mode') or 'automatic').strip().lower()
        wordpress_mode = {'one_click': 'automatic', 'standard': 'manual'}.get(wordpress_mode, wordpress_mode)
        wordpress_create_database = request.form.get('wordpress_create_database') == '1' or wordpress_mode == 'automatic'
        wordpress_title = (request.form.get('wordpress_title') or subdomain).strip()
        wordpress_admin = (request.form.get('wordpress_admin') or '').strip()
        wordpress_email = (request.form.get('wordpress_email') or '').strip()
        wordpress_password = request.form.get('wordpress_password') or ''
        wordpress_language = (request.form.get('wordpress_language') or 'uk').strip()

        if site_type not in allowed_types:
            flash('Unsupported site type.', 'error')
            return redirect(url_for('create_site_wizard'))
        if source_mode not in source_modes:
            flash('Unsupported source mode.', 'error')
            return redirect(url_for('create_site_wizard'))
        if site_type == 'php' and php_runtime not in allowed_php_versions:
            flash('Unsupported PHP version.', 'error')
            return redirect(url_for('create_site_wizard', site_type='php'))
        if site_type == 'wordpress':
            if wordpress_mode not in {'automatic', 'manual'} or wordpress_language not in {'uk', 'en_US'}:
                flash('Некоректні параметри WordPress.', 'error')
                return redirect(url_for('create_site_wizard', site_type='wordpress'))
            if wordpress_mode == 'automatic' and (not wordpress_admin or not valid_notification_email(wordpress_email) or len(wordpress_password) < 16):
                flash('Для one-click installation потрібні admin username, коректний email і пароль від 16 символів.', 'error')
                return redirect(url_for('create_site_wizard', site_type='wordpress'))
        if not validate_runtime_commands(install_command, build_command, start_command):
            flash('Runtime command contains a forbidden host-management operation.', 'error')
            return redirect(url_for('create_site_wizard', site_type=site_type))

        owner = user
        if user.is_admin and request.form.get('owner_id'):
            owner = db.session.get(User, request.form.get('owner_id', type=int))
            if not owner or owner.is_banned:
                flash(translate('site_owner_required'), 'error')
                return redirect(url_for('create_site_wizard'))

        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', subdomain):
            flash(translate('invalid_site_name'), 'error')
            return redirect(url_for('create_site_wizard'))
        if Site.query.filter_by(name=subdomain).first():
            flash(translate('subdomain_taken'), 'error')
            return redirect(url_for('create_site_wizard'))

        folder_name = f"{owner.username}_{subdomain}"
        site_path = os.path.join(app.config['UPLOAD_FOLDER'], folder_name)
        os.makedirs(site_path, exist_ok=True)
        if site_type == 'static':
            scaffold_site_content(site_path, subdomain)

        runtime_type = 'php' if site_type == 'wordpress' else site_type
        runtime_version = '8.3' if site_type == 'wordpress' else (php_runtime if runtime_type == 'php' else RUNTIME_VERSIONS[runtime_type][0])
        new_site = Site(
            name=subdomain,
            folder_name=folder_name,
            php_version=site_type,
            user_id=owner.id,
            runtime_type=runtime_type,
            application_type='wordpress' if site_type == 'wordpress' else 'custom',
            installation_mode=wordpress_mode if site_type == 'wordpress' else 'automatic',
            provisioning_phase='creating',
            runtime_version=runtime_version,
            runtime_status='configuring',
            deployment_status='pending',
            install_command=install_command,
            build_command=build_command,
            start_command=start_command,
            spa_enabled=spa_enabled,
        )
        if domain:
            new_site.custom_domain = domain
        db.session.add(new_site)
        db.session.commit()

        access = ensure_application_access(new_site)
        document_root = os.path.join(site_path, 'public_html')
        os.makedirs(document_root, exist_ok=True)
        if site_type == 'php':
            ensure_php_site_bootstrap(document_root, f'{subdomain}.myh.guru', runtime_version=runtime_version)
        stack_root = os.path.join(APP_STACKS_ROOT, folder_name)
        access.file_root = document_root
        access.upload_root = document_root
        access.deployment_root = stack_root
        new_site.provisioning_phase = 'provisioning_runtime'
        db.session.commit()
        try:
            if site_type == 'wordpress':
                if wordpress_mode == 'manual':
                    metadata = prepare_wordpress_runtime(stack_root, document_root, allocate_application_port(), populate_wordpress=False)
                else:
                    metadata = prepare_wordpress_runtime(stack_root, document_root, allocate_application_port())
            else:
                metadata = prepare_runtime(stack_root, document_root, runtime_type, runtime_version, allocate_application_port(),
                                           install_command=install_command, build_command=build_command, start_command=start_command,
                                           spa_enabled=spa_enabled)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            new_site.runtime_status = 'error'
            new_site.deployment_status = 'failed'
            new_site.provisioning_phase = 'failed_runtime'
            db.session.commit()
            log_action('site.runtime.provision.failed', f'{subdomain}: {mask_sensitive_text(str(exc))[:300]}')
            flash('Сайт збережено зі статусом Failed: середовище не вдалося підготувати. Ресурси можна безпечно видалити або повторити запуск після виправлення.', 'error')
            return redirect(url_for('manage_site', folder_name=folder_name))
        with open(os.path.join(stack_root, 'panel-metadata.json'), 'w', encoding='utf-8') as handle:
            json.dump({
                'site_type': site_type, 'source_mode': source_mode,
                'installation_mode': wordpress_mode if site_type == 'wordpress' else None,
                'generated_at': datetime.now().isoformat(timespec='seconds'),
                'runtime': {'language': runtime_type, 'version': runtime_version},
            }, handle, indent=2)
        new_site.internal_port = metadata['port']
        new_site.provisioning_phase = 'configuring_database' if site_type == 'wordpress' and wordpress_create_database else 'verifying'
        db.session.commit()
        if site_type == 'wordpress' and wordpress_create_database:
            database_name, database_user = generated_database_identifiers(owner.id, f'{subdomain}_wordpress')
            database_password = secrets.token_urlsafe(32)
            try:
                provision_mysql_database(database_name, database_user, database_password)
                resource = DatabaseResource(application_id=new_site.id, display_name='wordpress', engine='mysql', database_name=database_name,
                                            database_user=database_user, host=MYSQL_HOST, port=MYSQL_PORT, secret_ref='pending', created_by=user.id)
                db.session.add(resource); db.session.flush()
                resource.secret_ref = write_application_secret(new_site.id, 'database', resource.id, database_password)
                for key, value in {
                    'WORDPRESS_DB_HOST':f'{MYSQL_HOST}:{MYSQL_PORT}','WORDPRESS_DB_NAME':database_name,'WORDPRESS_DB_USER':database_user,
                    'WORDPRESS_DB_PASSWORD':database_password,'DB_HOST':MYSQL_HOST,'DB_PORT':str(MYSQL_PORT),'DB_NAME':database_name,
                    'DB_USER':database_user,'DB_PASSWORD':database_password,
                    'WORDPRESS_CONFIG_EXTRA':"define('DISALLOW_FILE_EDIT', true); define('DISABLE_WP_CRON', true); if (!empty($_SERVER['HTTP_X_FORWARDED_PROTO']) && $_SERVER['HTTP_X_FORWARDED_PROTO'] === 'https') { $_SERVER['HTTPS']='on'; }",
                }.items(): set_environment_value(new_site, user, key, value)
                db.session.commit(); sync_runtime_environment(new_site)
            except (RuntimeError, ValueError, pymysql.MySQLError) as exc:
                try: deprovision_mysql_database(database_name, database_user)
                except Exception: pass
                db.session.rollback(); new_site.runtime_status='error'; new_site.deployment_status='failed'; db.session.commit()
                new_site.provisioning_phase = 'failed_database'; db.session.commit()
                flash(f'WordPress database provisioning failed: {str(exc)[:300]}', 'error')
                return redirect(url_for('manage_site', folder_name=folder_name))
        if app.config.get('TESTING'):
            code, runtime_log, check = 0, 'test mode: runtime not started', {'ok': True}
        else:
            code, runtime_log = compose_action(stack_root, 'start')
            check = healthcheck(metadata, timeout=180 if site_type == 'wordpress' else 12) if code == 0 else {'ok': False}
        new_site.runtime_status = 'running' if check.get('ok') else 'error'
        new_site.deployment_status = 'success' if check.get('ok') else 'failed'
        new_site.last_restart_at = datetime.now() if check.get('ok') else None
        new_site.provisioning_phase = 'verifying' if check.get('ok') else 'failed_healthcheck'
        db.session.commit()
        if not check.get('ok'):
            try:
                compose_action(stack_root, 'delete')
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
                pass
            log_action('site.runtime.failed', f'{subdomain}: {runtime_log[-500:]}')
            flash('Сайт створено, але runtime не пройшов health check. Перевірте application logs.', 'error')
        else:
            log_action('site.runtime.started', f'{subdomain}:{runtime_type}:{runtime_version}:{metadata["port"]}')
            if site_type == 'wordpress' and wordpress_mode == 'automatic':
                try:
                    install_wordpress_one_click(new_site, access, wordpress_title, wordpress_admin, wordpress_email,
                                                wordpress_password, wordpress_language)
                    wordpress_password = ''
                    new_site.deployment_status = 'success'
                    new_site.provisioning_phase = 'ready'
                    db.session.commit()
                    log_action('wordpress.install.success', f'site={new_site.id}; mode=automatic; locale={wordpress_language}')
                except (RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                    wordpress_password = ''
                    new_site.runtime_status = 'error'
                    new_site.deployment_status = 'failed'
                    new_site.provisioning_phase = 'failed_install'
                    db.session.commit()
                    log_action('wordpress.install.failed', f'site={new_site.id}; phase=core_install')
                    flash('Автоматичне встановлення WordPress не вдалося. Режим automatic збережено; перегляньте логи та повторіть provisioning.', 'error')
            elif site_type == 'wordpress':
                new_site.provisioning_phase = 'needs_setup'; db.session.commit()
            elif check.get('ok'):
                new_site.provisioning_phase = 'ready'; db.session.commit()

        personal_sftp = SftpAccount.query.filter_by(assigned_user_id=owner.id).first()
        if personal_sftp:
            assigned_ids = set(parse_json_list(personal_sftp.assigned_applications_json, int))
            assigned_ids.add(new_site.id)
            personal_sftp.assigned_applications_json = dump_json_list(sorted(assigned_ids))
            db.session.commit()
            if personal_sftp.enabled:
                queue_sftp_provision(personal_sftp, 'assign', actor=user.username)

        log_action('site.create.wizard', f'{subdomain} type={site_type} source={source_mode} owner={owner.username}')
        flash(translate('site_created'), 'success')
        return redirect(url_for('manage_site', folder_name=folder_name))

    owners = User.query.filter_by(is_banned=False).order_by(User.username).all() if user.is_admin else [user]
    return render_template(
        'create_site_wizard.html',
        user=user,
        owners=owners,
        allowed_types=allowed_types,
        allowed_php_versions=allowed_php_versions,
        source_modes=source_modes,
        selected_type=selected_type,
        selected_source=selected_source,
        selected_php_version=selected_php_version,
        runtime_options=runtime_options,
    )


@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user = db.session.get(User, session['user_id'])
    if not user:
        session.clear()
        return redirect(url_for('login'))
    if user.is_banned:
        session.clear()
        return redirect(url_for('login'))
    
    if user_role(user) in {'admin', 'developer'} or user.is_admin:
        if user_role(user) == 'admin' or user.is_admin:
            all_sites = Site.query.order_by(Site.id.desc()).all()
        else:
            site_ids = assigned_application_ids(user)
            all_sites = Site.query.filter(Site.id.in_(site_ids)).order_by(Site.id.desc()).all() if site_ids else []
        metrics = get_server_metrics()
        services = [
            {'name': 'Панель', 'key': 'myh-guru'},
            {'name': 'Cloudflare Tunnel', 'key': 'cloudflared'},
            {'name': 'SSH', 'key': 'ssh'},
            {'name': 'Docker', 'key': 'docker'},
        ]
        service_statuses = {item['key']: get_service_status(item['key']) for item in services}
        users = User.query.filter_by(is_banned=False).order_by(User.username).all() if (user_role(user) == 'admin' or user.is_admin) else [user]
        usage = {item.id: user_usage_bytes(item) for item in users}
        recent_logs = AuditLog.query.order_by(AuditLog.id.desc()).limit(15).all()
        overview = dashboard_overview()
        admin_posture = admin_platform_status()
        return render_template('developer_dashboard.html', user=user, sites=all_sites, users=users, usage=usage, recent_logs=recent_logs, metrics=metrics, services=service_statuses, overview=overview, admin_posture=admin_posture, is_platform_admin=bool(user_role(user) == 'admin' or user.is_admin), can_create_site=user_has_role_permission(user, 'site.create'))

    user_sites = Site.query.filter_by(user_id=session['user_id']).all()
    actual_statuses = actual_site_runtime_statuses(user_sites)
    usage_bytes_value = user_usage_bytes(user)
    summary = {
        'sites': len(user_sites),
        'used_mb': round(usage_bytes_value / 1048576, 1),
        'domains': sum(1 for site in user_sites if site.custom_domain),
        'backups': sum(len(list_site_backups(site)) for site in user_sites),
        'databases': sum(len(site.database_resources) for site in user_sites),
        'running': sum(1 for site in user_sites if actual_statuses.get(site.id) == 'running'),
        'attention': sum(1 for site in user_sites if actual_statuses.get(site.id) in {'error', 'stopped'} or site.deployment_status == 'failed'),
    }
    attention_sites = [
        {'site': site, 'runtime_status': actual_statuses.get(site.id, 'unknown'), 'deployment_failed': site.deployment_status == 'failed'}
        for site in user_sites
        if actual_statuses.get(site.id) in {'error', 'stopped'} or site.deployment_status == 'failed'
    ]
    recent_activity = AuditLog.query.filter_by(user_id=user.id).order_by(AuditLog.created_at.desc()).limit(6).all()
    return render_template('dashboard.html', user=user, sites=user_sites, usage_bytes=usage_bytes_value, summary=summary, attention_sites=attention_sites, recent_activity=recent_activity, can_create_site=user_has_role_permission(user, 'site.create'), can_sftp_access=True)


@app.route('/developer/dashboard')
@admin_required
def developer_dashboard():
    return redirect(url_for('dashboard'))


@app.route('/api/platform/status')
@admin_required
def api_platform_status():
    return jsonify(admin_platform_status())

# --- НОВІ МАРШРУТИ РЕЖИМУ РОЗРОБНИКА (Крок 2) ---

@app.route('/developer/users')
@admin_required
def developer_users():
    users = User.query.order_by(User.username).all()
    sites = Site.query.order_by(Site.id.desc()).all()
    usage = {user.id: user_usage_bytes(user) for user in users}
    logs = AuditLog.query.order_by(AuditLog.id.desc()).limit(100).all()
    application_rows = [application_summary_for_user(None, site) for site in sites]
    sftp_accounts = SftpAccount.query.order_by(SftpAccount.username.asc()).all()
    return render_template('developer_users.html', users=users, usage=usage, logs=logs, sites=sites, application_rows=application_rows, sftp_accounts=sftp_accounts)


@app.route('/developer/users/create', methods=['POST'])
@admin_required
def developer_create_user():
    username = request.form.get('username', '').strip().lower()
    first_name = request.form.get('first_name', '').strip()
    last_name = request.form.get('last_name', '').strip()
    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')
    quota_mb = request.form.get('quota_mb', type=int) or 51200
    role = (request.form.get('role') or 'user').strip().lower()
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{2,31}', username):
        flash(translate('invalid_username'), 'error')
    elif not first_name or not last_name or '@' not in email:
        flash(translate('invalid_user_profile'), 'error')
    elif len(password) < 12:
        flash(translate('password_short'), 'error')
    elif not 64 <= quota_mb <= 51200:
        flash(translate('quota_invalid'), 'error')
    elif role not in {'user', 'developer', 'admin'}:
        flash('Непідтримувана роль.', 'error')
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
            is_admin=(role == 'admin'),
            role=role,
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
    selected_role = (request.form.get('role') or user.role or ('admin' if user.is_admin else 'user')).strip().lower()
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
    if selected_role not in {'user', 'developer', 'admin'}:
        flash('Непідтримувана роль.', 'error')
        return redirect(url_for('developer_users'))
    user.first_name = first_name
    user.last_name = last_name
    user.email = email
    user.quota_mb = quota_mb
    user.must_change_password = must_change_password
    user.role = selected_role
    user.is_admin = selected_role == 'admin'
    db.session.commit()
    log_action('user.update', f'{user.username}: quota={quota_mb} must_change={must_change_password} role={selected_role}')
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
    user.role = 'admin' if user.is_admin else 'user'
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
        access = ApplicationAccess.query.filter_by(site_id=site.id).first()
        if access:
            db.session.delete(access)
        db.session.delete(site)

    for account in SftpAccount.query.filter_by(assigned_user_id=user.id).all():
        db.session.delete(account)

    UploadHistory.query.filter_by(user_id=user.id).delete(synchronize_session=False)

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
            access = ApplicationAccess.query.filter_by(site_id=site.id).first()
            if access:
                db.session.delete(access)
            db.session.delete(site)
            changed += 1
    db.session.commit()
    log_action('site.bulk', f'action={action} count={changed}')
    flash(f'Bulk-операція виконана: {action} ({changed}).', 'success')
    return redirect(url_for('developer_users'))


def current_user_sites(user):
    site_ids = assigned_application_ids(user)
    return Site.query.filter(Site.id.in_(site_ids)).order_by(Site.created_at.desc()).all() if site_ids else []


def system_sftp_inventory(panel_accounts):
    try:
        group = grp.getgrnam('myh_sftp')
    except KeyError:
        return []
    panel_by_name = {account.username: account for account in panel_accounts}
    rows = []
    for entry in pwd.getpwall():
        if entry.pw_gid != group.gr_gid:
            continue
        root = f'/srv/apps/{entry.pw_name}'
        upload = os.path.join(root, 'upload')
        key_file = f'/etc/ssh/myh-sftp-authorized-keys/{entry.pw_name}'
        panel_account = panel_by_name.get(entry.pw_name)
        site_directories = []
        sites_root = os.path.join(upload, 'sites')
        if os.path.isdir(sites_root):
            site_directories = sorted(os.listdir(sites_root))[:20]
        activity_times = [os.path.getmtime(path) for path in (root, upload, key_file) if os.path.exists(path)]
        last_activity = max(activity_times) if activity_times else None
        if panel_account:
            classification, removable = 'PRODUCTION', False
        elif site_directories:
            classification, removable = 'UNKNOWN', False
        else:
            classification, removable = 'UNKNOWN', False
        rows.append({
            'username': entry.pw_name, 'uid': entry.pw_uid, 'group': group.gr_name, 'home': entry.pw_dir,
            'chroot': root, 'sites': site_directories, 'panel_owner': panel_account.assigned_user.username if panel_account and panel_account.assigned_user else None,
            'last_activity': datetime.fromtimestamp(last_activity) if last_activity else None,
            'classification': classification, 'safe_to_remove': removable,
            'authorized_keys': 1 if os.path.isfile(key_file) and os.path.getsize(key_file) else 0,
        })
    return sorted(rows, key=lambda item: item['username'])


def probe_domain_status(domain):
    """Resolve DNS and complete a hostname-verified TLS handshake."""
    result = {'dns': 'pending', 'ssl': 'pending', 'detail': '', 'valid_until': None}
    try:
        addresses = sorted({item[4][0] for item in socket.getaddrinfo(domain, 443, type=socket.SOCK_STREAM)})
        result['dns'] = 'verified' if addresses else 'dns_error'
        result['addresses'] = addresses[:4]
    except socket.gaierror as exc:
        result.update(dns='dns_error', ssl='failed', detail=f'DNS lookup failed: {exc}')
        return result
    try:
        context = ssl.create_default_context()
        with socket.create_connection((domain, 443), timeout=4) as raw_socket:
            with context.wrap_socket(raw_socket, server_hostname=domain) as tls_socket:
                certificate = tls_socket.getpeercert()
        valid_until = certificate.get('notAfter')
        result['valid_until'] = valid_until
        remaining = ssl.cert_time_to_seconds(valid_until) - time.time() if valid_until else 0
        result['ssl'] = 'expiring' if remaining < 30 * 86400 else 'secure'
    except (OSError, ssl.SSLError, ValueError) as exc:
        result.update(ssl='failed', detail=f'TLS verification failed: {exc}')
    return result


def edge_tls_certificate_status(domain='myh.guru'):
    result = {'provider': 'Cloudflare', 'management': 'Managed by Cloudflare', 'status': 'failed',
              'expires': None, 'days_remaining': None, 'issuer': None, 'origin_transport': 'Cloudflare Tunnel over loopback HTTP'}
    try:
        context = ssl.create_default_context()
        with socket.create_connection((domain, 443), timeout=5) as raw_socket:
            with context.wrap_socket(raw_socket, server_hostname=domain) as tls_socket:
                certificate = tls_socket.getpeercert()
        expiry = certificate.get('notAfter')
        remaining = int((ssl.cert_time_to_seconds(expiry) - time.time()) / 86400) if expiry else None
        issuer = dict(item[0] for item in certificate.get('issuer', ()))
        result.update(status='valid' if remaining is not None and remaining >= 0 else 'failed', expires=expiry,
                      days_remaining=remaining, issuer=issuer.get('organizationName') or issuer.get('commonName'))
        if remaining is not None and remaining < 30:
            result['status'] = 'expiring'
    except (OSError, ssl.SSLError, ValueError):
        result['detail'] = 'Edge TLS validation failed'
    return result


@app.route('/sites')
def user_sites_index():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    if not user or user.is_banned: abort(403)
    sites = current_user_sites(user)
    latest_deployments = {}
    for event in DeploymentEvent.query.filter(DeploymentEvent.site_name.in_([site.name for site in sites])).order_by(DeploymentEvent.created_at.desc()).all() if sites else []:
        latest_deployments.setdefault(event.site_name, event)
    return render_template('sites.html', user=user, sites=sites, actual_statuses=actual_site_runtime_statuses(sites), latest_deployments=latest_deployments, can_create_site=user_has_role_permission(user, 'site.create'))


@app.route('/domains')
def user_domains_index():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    if not user or user.is_banned: abort(403)
    sites = current_user_sites(user)
    domain_statuses = {site.id: probe_domain_status(site.custom_domain or f'{site.name}.myh.guru') for site in sites}
    return render_template('domains.html', user=user, sites=sites, domain_statuses=domain_statuses)


@app.route('/databases')
def user_databases_index():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    if not user or user.is_banned: abort(403)
    sites = current_user_sites(user); site_ids = [site.id for site in sites]
    databases = DatabaseResource.query.filter(DatabaseResource.application_id.in_(site_ids)).order_by(DatabaseResource.created_at.desc()).all() if site_ids else []
    sizes = {}
    backups = {}
    for resource in databases:
        try: sizes[resource.id] = database_size_bytes(resource)
        except pymysql.MySQLError: sizes[resource.id] = None
        path = os.path.join(MYSQL_BACKUP_ROOT, str(resource.application.user_id), str(resource.id))
        backups[resource.id] = sorted([name for name in os.listdir(path) if re.fullmatch(r'\d{8}-\d{6}\.sql', name)], reverse=True) if os.path.isdir(path) else []
    mysql_available = False
    mysql_version = None
    try:
        with mysql_provision_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT VERSION() AS version')
                version_row = cursor.fetchone()
        mysql_available = True
        if isinstance(version_row, dict):
            mysql_version = str(version_row.get('version') or '').split('-', 1)[0] or None
        elif version_row:
            mysql_version = str(version_row[0]).split('-', 1)[0] or None
    except pymysql.MySQLError:
        pass
    database_engines = [
        {'id': 'mysql', 'name': 'MySQL', 'available': mysql_available, 'version': mysql_version,
         'description_uk': 'Популярна база даних для WordPress, PHP, Laravel та багатьох вебзастосунків.',
         'description_en': 'A popular database for WordPress, PHP, Laravel and many web applications.'},
        {'id': 'postgresql', 'name': 'PostgreSQL', 'available': False, 'version': None,
         'description_uk': 'Потужна база даних для Django, FastAPI, Node.js та сучасних вебзастосунків.',
         'description_en': 'A powerful database for Django, FastAPI, Node.js and modern web applications.'},
    ]
    return render_template('databases.html', user=user, sites=sites, databases=databases, database_sizes=sizes,
                           database_backups=backups, database_engines=database_engines)


@app.route('/databases/<int:resource_id>/studio')
def database_studio(resource_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    user = db.session.get(User, session['user_id']); resource = database_owner_or_404(resource_id, user)
    tables = []
    version = None
    try:
        with mysql_tenant_connection(resource, dictionary=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT VERSION() AS version')
                version = cursor.fetchone()['version'].split('-', 1)[0]
                cursor.execute("""SELECT table_name AS name,table_rows AS row_count,
                                  data_length+index_length AS size_bytes,engine,update_time AS updated
                                  FROM information_schema.tables WHERE table_schema=%s ORDER BY table_name""",
                               (resource.database_name,))
                tables = cursor.fetchall()
    except (pymysql.MySQLError, RuntimeError):
        resource.status = 'error'
    backup_rows = []
    path = database_backup_directory(resource)
    for name in sorted((item for item in os.listdir(path) if re.fullmatch(r'\d{8}-\d{6}\.sql', item)), reverse=True):
        backup_path = os.path.join(path, name)
        checksum_path = backup_path + '.sha256'
        backup_rows.append({'name': name, 'size': os.path.getsize(backup_path), 'checksum': os.path.isfile(checksum_path),
                            'created': datetime.fromtimestamp(os.path.getmtime(backup_path))})
    try: size_bytes = database_size_bytes(resource)
    except (pymysql.MySQLError, RuntimeError): size_bytes = 0
    sql_history = AuditLog.query.filter(AuditLog.user_id == user.id, AuditLog.action.in_(['sql.execute', 'sql.execute.failed']),
                                        AuditLog.detail.like(f'database={resource.id};%')).order_by(AuditLog.created_at.desc()).limit(10).all()
    return render_template('database_studio.html', resource=resource, tables=tables, version=version,
                           size_bytes=size_bytes, backups=backup_rows, sql_history=sql_history)


@app.route('/api/databases/<int:resource_id>/tables')
def api_database_tables(resource_id):
    if 'user_id' not in session: abort(401)
    user = db.session.get(User, session['user_id']); resource = database_owner_or_404(resource_id, user)
    with mysql_tenant_connection(resource, dictionary=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("""SELECT table_name AS name,table_rows AS rowCount,data_length+index_length AS sizeBytes,
                              engine,update_time AS updatedAt FROM information_schema.tables
                              WHERE table_schema=%s ORDER BY table_name""", (resource.database_name,))
            rows = cursor.fetchall()
    for row in rows: row['updatedAt'] = json_database_value(row['updatedAt'])
    return jsonify({'tables': rows, 'requestId': g.request_id})


@app.route('/api/databases/<int:resource_id>/tables', methods=['POST'])
def api_database_table_create(resource_id):
    if 'user_id' not in session: abort(401)
    user = db.session.get(User, session['user_id']); resource = database_owner_or_404(resource_id, user)
    payload = request.get_json(silent=True) or {}; table_name = quote_mysql_identifier(payload.get('name'))
    columns = payload.get('columns') if isinstance(payload.get('columns'), list) else []
    definitions = []
    for item in columns[:50]:
        if not isinstance(item, dict): abort(400, 'Invalid column definition.')
        column_type = str(item.get('type') or '').upper()
        if not STUDIO_COLUMN_TYPE.fullmatch(column_type): abort(400, 'Unsupported column type.')
        definition = f'{quote_mysql_identifier(item.get("name"))} {column_type} {"NULL" if item.get("nullable", True) else "NOT NULL"}'
        if item.get('primary'): definition += ' PRIMARY KEY'
        if item.get('autoIncrement') and re.search(r'INT', column_type): definition += ' AUTO_INCREMENT'
        definitions.append(definition)
    if not definitions: abort(400, 'At least one column is required.')
    with mysql_tenant_connection(resource) as connection:
        with connection.cursor() as cursor: cursor.execute(f'CREATE TABLE {table_name} ({",".join(definitions)})')
        connection.commit()
    log_action('table.create', f'database={resource.id}; table={payload.get("name")}')
    return jsonify({'ok': True, 'requestId': g.request_id}), 201


@app.route('/api/databases/<int:resource_id>/tables/<table_name>/structure')
def api_database_table_structure(resource_id, table_name):
    if 'user_id' not in session: abort(401)
    user = db.session.get(User, session['user_id']); resource = database_owner_or_404(resource_id, user)
    return jsonify({**mysql_table_structure(resource, table_name), 'requestId': g.request_id})


@app.route('/api/databases/<int:resource_id>/tables/<table_name>/data')
def api_database_table_data(resource_id, table_name):
    if 'user_id' not in session: abort(401)
    user = db.session.get(User, session['user_id']); resource = database_owner_or_404(resource_id, user)
    page = max(1, request.args.get('page', 1, type=int)); per_page = max(10, min(request.args.get('perPage', 50, type=int), 100))
    search = (request.args.get('search') or '').strip()[:120]
    with mysql_tenant_connection(resource, dictionary=True) as connection:
        require_mysql_table(resource, table_name, connection)
        structure = mysql_table_structure(resource, table_name, connection)
        column_names = [row['column_name'] for row in structure['columns']]
        sort = request.args.get('sort') if request.args.get('sort') in column_names else (column_names[0] if column_names else None)
        direction = 'DESC' if request.args.get('direction', '').lower() == 'desc' else 'ASC'
        searchable = [row['column_name'] for row in structure['columns'] if any(t in row['column_type'].lower() for t in ('char', 'text'))][:8]
        where_sql = ''; params = []
        if search and searchable:
            where_sql = ' WHERE ' + ' OR '.join(f'CAST({quote_mysql_identifier(name)} AS CHAR) LIKE %s' for name in searchable)
            params = [f'%{search}%'] * len(searchable)
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT COUNT(*) AS count FROM {quote_mysql_identifier(table_name)}{where_sql}', params)
            total = int(cursor.fetchone()['count'])
            order_sql = f' ORDER BY {quote_mysql_identifier(sort)} {direction}' if sort else ''
            cursor.execute(f'SELECT * FROM {quote_mysql_identifier(table_name)}{where_sql}{order_sql} LIMIT %s OFFSET %s',
                           params + [per_page, (page - 1) * per_page])
            rows = [{key: json_database_value(value) for key, value in row.items()} for row in cursor.fetchall()]
    return jsonify({'columns': column_names, 'rows': rows, 'page': page, 'perPage': per_page, 'total': total,
                    'primaryKey': [row['column_name'] for row in structure['columns'] if row['column_key'] == 'PRI'],
                    'requestId': g.request_id})


@app.route('/api/databases/<int:resource_id>/sql', methods=['POST'])
def api_database_sql(resource_id):
    if 'user_id' not in session: abort(401)
    user = db.session.get(User, session['user_id']); resource = database_owner_or_404(resource_id, user)
    payload = request.get_json(silent=True) or {}; statement = str(payload.get('sql') or '').strip()
    if not statement or len(statement) > 20000: abort(400, 'SQL statement is required and must be at most 20,000 characters.')
    trimmed = statement.rstrip().rstrip(';')
    if ';' in trimmed: abort(400, 'Run one SQL statement at a time.')
    if STUDIO_BLOCKED_SQL.search(trimmed): abort(403, 'Server-level SQL is not allowed in Database Studio.')
    dangerous = bool(STUDIO_DESTRUCTIVE_SQL.search(trimmed))
    if dangerous and payload.get('confirmDangerous') is not True:
        return jsonify({'error': {'code': 'CONFIRMATION_REQUIRED', 'message': 'Confirm this destructive SQL statement.', 'requestId': g.request_id}}), 409
    started = time.monotonic()
    try:
        with mysql_tenant_connection(resource, dictionary=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute('SET SESSION MAX_EXECUTION_TIME=5000')
                cursor.execute(trimmed)
                columns = [item[0] for item in cursor.description] if cursor.description else []
                result_rows = cursor.fetchmany(201) if columns else []
                truncated = len(result_rows) > 200
                result_rows = result_rows[:200]
                serialized = [{key: json_database_value(value) for key, value in row.items()} for row in result_rows]
                if len(json.dumps(serialized, ensure_ascii=False)) > 1024 * 1024:
                    serialized = serialized[:25]; truncated = True
                affected = cursor.rowcount
            connection.commit()
    except pymysql.MySQLError as exc:
        log_action('sql.execute.failed', f'database={resource.id}; class={exc.__class__.__name__}')
        return jsonify({'error': {'code': 'SQL_EXECUTION_FAILED', 'message': 'Не вдалося виконати SQL-запит.',
                                  'details': mask_sensitive_text(str(exc))[:300], 'requestId': g.request_id}}), 400
    elapsed = round((time.monotonic() - started) * 1000, 1)
    log_action('sql.execute', f'database={resource.id}; kind={trimmed.split(None, 1)[0].upper()}; affected={affected}; ms={elapsed}')
    return jsonify({'columns': columns, 'rows': serialized, 'affectedRows': affected, 'executionMs': elapsed,
                    'truncated': truncated, 'requestId': g.request_id})


@app.route('/api/databases/<int:resource_id>/tables/<table_name>/rows', methods=['POST', 'PATCH', 'DELETE'])
def api_database_table_rows(resource_id, table_name):
    if 'user_id' not in session: abort(401)
    user = db.session.get(User, session['user_id']); resource = database_owner_or_404(resource_id, user)
    payload = request.get_json(silent=True) or {}
    with mysql_tenant_connection(resource, dictionary=True) as connection:
        require_mysql_table(resource, table_name, connection)
        structure = mysql_table_structure(resource, table_name, connection)
        columns = {row['column_name'] for row in structure['columns']}
        primary = [row['column_name'] for row in structure['columns'] if row['column_key'] == 'PRI']
        values = payload.get('values') if isinstance(payload.get('values'), dict) else {}
        clean_values = {key: value for key, value in values.items() if key in columns}
        with connection.cursor() as cursor:
            if request.method == 'POST':
                if not clean_values: abort(400, 'At least one column value is required.')
                names = list(clean_values); cursor.execute(
                    f'INSERT INTO {quote_mysql_identifier(table_name)} ({",".join(quote_mysql_identifier(n) for n in names)}) VALUES ({",".join(["%s"] * len(names))})',
                    [clean_values[name] for name in names])
                action = 'insert'; affected = cursor.rowcount
            else:
                key_values = payload.get('primaryKey') if isinstance(payload.get('primaryKey'), dict) else {}
                if not primary or any(name not in key_values for name in primary): abort(409, 'A complete primary key is required.')
                where = ' AND '.join(f'{quote_mysql_identifier(name)}=%s' for name in primary)
                where_values = [key_values[name] for name in primary]
                if request.method == 'PATCH':
                    if not clean_values: abort(400, 'At least one column value is required.')
                    names = list(clean_values); cursor.execute(
                        f'UPDATE {quote_mysql_identifier(table_name)} SET {",".join(f"{quote_mysql_identifier(n)}=%s" for n in names)} WHERE {where} LIMIT 1',
                        [clean_values[name] for name in names] + where_values)
                    action = 'edit'; affected = cursor.rowcount
                else:
                    if payload.get('confirm') is not True: abort(409, 'Row deletion requires confirmation.')
                    cursor.execute(f'DELETE FROM {quote_mysql_identifier(table_name)} WHERE {where} LIMIT 1', where_values)
                    action = 'delete'; affected = cursor.rowcount
        connection.commit()
    log_action(f'table.row.{action}', f'database={resource.id}; table={table_name}; affected={affected}')
    return jsonify({'affectedRows': affected, 'requestId': g.request_id})


@app.route('/api/databases/<int:resource_id>/tables/<table_name>/structure', methods=['POST', 'PATCH', 'DELETE'])
def api_database_table_structure_change(resource_id, table_name):
    if 'user_id' not in session: abort(401)
    user = db.session.get(User, session['user_id']); resource = database_owner_or_404(resource_id, user)
    payload = request.get_json(silent=True) or {}; action = str(payload.get('action') or '')
    if action in {'drop_column', 'drop_table'} and payload.get('confirm') is not True: abort(409, 'Destructive structure changes require confirmation.')
    with mysql_tenant_connection(resource, dictionary=True) as connection:
        require_mysql_table(resource, table_name, connection)
        with connection.cursor() as cursor:
            if action == 'add_column':
                column = quote_mysql_identifier(payload.get('name')); column_type = str(payload.get('type') or '').upper()
                if not STUDIO_COLUMN_TYPE.fullmatch(column_type): abort(400, 'Unsupported column type.')
                nullable = 'NULL' if payload.get('nullable', True) else 'NOT NULL'
                cursor.execute(f'ALTER TABLE {quote_mysql_identifier(table_name)} ADD COLUMN {column} {column_type} {nullable}')
            elif action == 'edit_column':
                old_name = quote_mysql_identifier(payload.get('oldName')); new_name = quote_mysql_identifier(payload.get('name'))
                column_type = str(payload.get('type') or '').upper()
                if not STUDIO_COLUMN_TYPE.fullmatch(column_type): abort(400, 'Unsupported column type.')
                nullable = 'NULL' if payload.get('nullable', True) else 'NOT NULL'
                cursor.execute(f'ALTER TABLE {quote_mysql_identifier(table_name)} CHANGE COLUMN {old_name} {new_name} {column_type} {nullable}')
            elif action == 'drop_column':
                cursor.execute(f'ALTER TABLE {quote_mysql_identifier(table_name)} DROP COLUMN {quote_mysql_identifier(payload.get("name"))}')
            elif action == 'create_index':
                names = payload.get('columns') if isinstance(payload.get('columns'), list) else []
                structure = mysql_table_structure(resource, table_name, connection); allowed = {row['column_name'] for row in structure['columns']}
                if not names or any(name not in allowed for name in names): abort(400, 'Invalid index columns.')
                cursor.execute(f'CREATE INDEX {quote_mysql_identifier(payload.get("name"))} ON {quote_mysql_identifier(table_name)} ({",".join(quote_mysql_identifier(n) for n in names)})')
            elif action == 'drop_table':
                cursor.execute(f'DROP TABLE {quote_mysql_identifier(table_name)}')
            else: abort(400, 'Unsupported structure action.')
        connection.commit()
    log_action('table.delete' if action == 'drop_table' else 'table.alter', f'database={resource.id}; table={table_name}; action={action}')
    return jsonify({'ok': True, 'requestId': g.request_id})


@app.route('/databases/create', methods=['POST'])
def database_create():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    site = db.session.get(Site, request.form.get('site_id', type=int))
    require_application_permission(user, site, 'database.create')
    return site_database_create(site.id)


@app.route('/databases/<int:resource_id>/reset', methods=['POST'])
def database_reset(resource_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    user = db.session.get(User, session['user_id']); resource = database_owner_or_404(resource_id, user)
    password = secrets.token_urlsafe(32)
    reset_mysql_password(resource.database_user, password)
    resource.secret_ref = write_application_secret(resource.application_id, 'database', resource.id, password)
    for key, value in {'DB_PASSWORD': password, 'DATABASE_URL': f'mysql://{resource.database_user}:{urllib.parse.quote(password, safe="")}@{resource.host}:{resource.port}/{resource.database_name}'}.items():
        set_environment_value(resource.application, user, key, value)
    db.session.commit(); sync_runtime_environment(resource.application)
    log_action('database.credentials.reset', resource.database_name)
    flash('Database credentials reset and runtime secrets updated.', 'success')
    return redirect(url_for('user_databases_index'))


@app.route('/databases/<int:resource_id>/delete', methods=['POST'])
def database_delete(resource_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    user = db.session.get(User, session['user_id']); resource = database_owner_or_404(resource_id, user)
    name = resource.database_name
    deprovision_mysql_database(resource.database_name, resource.database_user)
    db.session.delete(resource); db.session.commit()
    log_action('database.delete', name); flash('Database and its MySQL account were deleted.', 'success')
    return redirect(url_for('user_databases_index'))


@app.route('/databases/<int:resource_id>/backup', methods=['POST'])
def database_backup(resource_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    user = db.session.get(User, session['user_id']); resource = database_owner_or_404(resource_id, user)
    path = create_database_backup(resource)
    log_action('database.backup', f'{resource.database_name}:{os.path.basename(path)}')
    flash('Database backup created.', 'success'); return redirect(url_for('user_databases_index'))


@app.route('/databases/<int:resource_id>/backups/<backup_name>/download')
def database_backup_download(resource_id, backup_name):
    if 'user_id' not in session: return redirect(url_for('login'))
    user = db.session.get(User, session['user_id']); resource = database_owner_or_404(resource_id, user)
    if not re.fullmatch(r'\d{8}-\d{6}\.sql', backup_name): abort(404)
    path = os.path.realpath(os.path.join(database_backup_directory(resource), backup_name))
    if os.path.dirname(path) != os.path.realpath(database_backup_directory(resource)) or not os.path.isfile(path): abort(404)
    return send_file(path, as_attachment=True, download_name=f'{resource.display_name}-{backup_name}')


@app.route('/databases/<int:resource_id>/backups/<backup_name>/delete', methods=['POST'])
def database_backup_delete(resource_id, backup_name):
    if 'user_id' not in session: return redirect(url_for('login'))
    user = db.session.get(User, session['user_id']); resource = database_owner_or_404(resource_id, user)
    if not re.fullmatch(r'\d{8}-\d{6}\.sql', backup_name): abort(404)
    path = os.path.realpath(os.path.join(database_backup_directory(resource), backup_name))
    if os.path.dirname(path) != os.path.realpath(database_backup_directory(resource)) or not os.path.isfile(path): abort(404)
    os.unlink(path)
    try:
        os.unlink(path + '.sha256')
    except FileNotFoundError:
        pass
    log_action('database.backup.delete', f'{resource.database_name}:{backup_name}')
    flash('Database backup deleted.', 'success'); return redirect(url_for('user_databases_index'))


@app.route('/databases/<int:resource_id>/backups/<backup_name>/restore', methods=['POST'])
def database_backup_restore(resource_id, backup_name):
    if 'user_id' not in session: return redirect(url_for('login'))
    user = db.session.get(User, session['user_id']); resource = database_owner_or_404(resource_id, user)
    if request.form.get('confirm') != resource.database_name: abort(400)
    restore_database_backup(resource, backup_name)
    log_action('database.restore', f'{resource.database_name}:{backup_name}')
    flash('Database restored.', 'success'); return redirect(url_for('user_databases_index'))


@app.route('/databases/<int:resource_id>/import', methods=['POST'])
def database_studio_import(resource_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    user = db.session.get(User, session['user_id']); resource = database_owner_or_404(resource_id, user)
    uploaded = request.files.get('database_file')
    if not uploaded or not uploaded.filename: abort(400, 'SQL import file is required.')
    filename = secure_filename(uploaded.filename); is_gzip = filename.lower().endswith('.sql.gz')
    if not (filename.lower().endswith('.sql') or is_gzip): abort(400, 'Only .sql and .sql.gz files are supported.')
    temporary = tempfile.NamedTemporaryFile(prefix='myh-db-import-', suffix='.sql', delete=False)
    total = 0
    try:
        source = gzip.GzipFile(fileobj=uploaded.stream, mode='rb') if is_gzip else uploaded.stream
        with temporary:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk: break
                total += len(chunk)
                if total > 32 * 1024 * 1024: abort(413, 'Expanded SQL import exceeds 32 MB.')
                temporary.write(chunk)
        defaults = database_client_defaults(resource)
        try:
            with open(temporary.name, 'rb') as input_file:
                process = subprocess.run(['mysql', f'--defaults-extra-file={defaults}', resource.database_name],
                                         stdin=input_file, stderr=subprocess.PIPE, timeout=300, check=False)
            if process.returncode: raise RuntimeError('Database import failed.')
        finally:
            os.unlink(defaults)
        log_action('database.import', f'database={resource.id}; bytes={total}')
        flash('Database import completed.', 'success')
    except (OSError, EOFError, gzip.BadGzipFile, subprocess.TimeoutExpired, RuntimeError):
        flash('Не вдалося імпортувати базу даних.', 'error')
    finally:
        try: os.unlink(temporary.name)
        except FileNotFoundError: pass
    return redirect(url_for('database_studio', resource_id=resource.id) + '#import-export')


@app.route('/databases/<int:resource_id>/export')
def database_studio_export(resource_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    user = db.session.get(User, session['user_id']); resource = database_owner_or_404(resource_id, user)
    export_type = request.args.get('type', 'all')
    options = {'structure': ['--no-data'], 'data': ['--no-create-info'], 'all': []}
    if export_type not in options: abort(400, 'Invalid export type.')
    destination = tempfile.NamedTemporaryFile(prefix='myh-db-export-', suffix='.sql', delete=False)
    destination.close(); defaults = database_client_defaults(resource)
    try:
        with open(destination.name, 'wb') as output:
            process = subprocess.run(['mysqldump', f'--defaults-extra-file={defaults}', '--single-transaction',
                                      '--triggers', '--no-tablespaces', *options[export_type], resource.database_name],
                                     stdout=output, stderr=subprocess.PIPE, timeout=300, check=False)
        if process.returncode: raise RuntimeError('Database export failed.')
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        try: os.unlink(destination.name)
        except FileNotFoundError: pass
        abort(500, 'Не вдалося експортувати базу даних.')
    finally:
        os.unlink(defaults)
    @after_this_request
    def cleanup_export(response):
        try: os.unlink(destination.name)
        except FileNotFoundError: pass
        return response
    log_action('database.export', f'database={resource.id}; type={export_type}')
    return send_file(destination.name, as_attachment=True, download_name=f'{resource.display_name}-{export_type}.sql')


@app.route('/api/databases')
def api_databases():
    if 'user_id' not in session: abort(401)
    user = db.session.get(User, session['user_id']); sites = current_user_sites(user); site_ids = [site.id for site in sites]
    rows = DatabaseResource.query.filter(DatabaseResource.application_id.in_(site_ids)).all() if site_ids else []
    return jsonify([{'id': r.id, 'name': r.display_name, 'physicalName': r.database_name, 'engine': r.engine,
                     'siteId': r.application_id, 'status': r.status, 'host': r.host, 'port': r.port,
                     'username': r.database_user, 'createdAt': r.created_at.isoformat()} for r in rows])


@app.route('/api/databases/<int:resource_id>')
def api_database(resource_id):
    if 'user_id' not in session: abort(401)
    user = db.session.get(User, session['user_id']); r = database_owner_or_404(resource_id, user)
    return jsonify({'id': r.id, 'name': r.display_name, 'physicalName': r.database_name, 'engine': r.engine,
                    'siteId': r.application_id, 'status': r.status, 'host': r.host, 'port': r.port,
                    'username': r.database_user, 'createdAt': r.created_at.isoformat()})


@app.route('/api/mysql/health')
def api_mysql_health():
    if 'user_id' not in session: abort(401)
    user = db.session.get(User, session['user_id'])
    if not user or not user.is_admin: abort(403)
    now = time.monotonic()
    if now - MYSQL_HEALTH_CACHE['checked_at'] >= 10:
        try:
            with mysql_provision_connection() as connection:
                with connection.cursor() as cursor: cursor.execute('SELECT 1')
            MYSQL_HEALTH_CACHE.update(checked_at=now, ok=True)
        except pymysql.MySQLError:
            MYSQL_HEALTH_CACHE.update(checked_at=now, ok=False)
    return jsonify({'service': 'mysql', 'ok': MYSQL_HEALTH_CACHE['ok']}), (200 if MYSQL_HEALTH_CACHE['ok'] else 503)


@app.route('/developer/databases')
@admin_required
def developer_databases():
    mysql_status = {'available': False, 'version': None, 'connections': None, 'resources': DatabaseResource.query.filter_by(engine='mysql').count(), 'size': 0}
    try:
        with mysql_provision_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT VERSION()')
                mysql_status['version'] = str(cursor.fetchone()[0]).split('-', 1)[0]
                cursor.execute("SHOW STATUS LIKE 'Threads_connected'")
                mysql_status['connections'] = int(cursor.fetchone()[1])
        mysql_status['available'] = True
        mysql_status['size'] = sum(database_size_bytes(item) for item in DatabaseResource.query.filter_by(engine='mysql').all())
    except (pymysql.MySQLError, RuntimeError):
        pass
    return render_template('developer_databases.html', mysql_status=mysql_status,
                           postgres_status={'available': False, 'version': None, 'connections': None, 'resources': 0, 'size': 0})


@app.route('/backups')
def user_backups_index():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    if not user or user.is_banned: abort(403)
    rows = []
    for site in current_user_sites(user):
        rows.extend({'site': site, 'backup': backup} for backup in list_site_backups(site))
    return render_template('user_backups.html', user=user, rows=rows)


@app.route('/logs')
def user_logs_index():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    if not user or user.is_banned: abort(403)
    lines = max(50, min(request.args.get('lines', 200, type=int), 500))
    log_lines = collect_application_logs_for_user(user, lines)
    return render_template('user_logs.html', user=user, log_lines=log_lines, lines=lines,
                           log_issues=classify_log_issues(log_lines))


@app.route('/profile')
def user_profile():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    if not user or user.is_banned: abort(403)
    return render_template('profile.html', user=user, usage_bytes=user_usage_bytes(user))


@app.route('/dashboard/sftp-access')
def user_sftp_access():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    if not user:
        abort(403)
    require_role_permission(user, 'sftp.access')

    site_ids = assigned_application_ids(user)
    sites = Site.query.filter(Site.id.in_(site_ids)).order_by(Site.name.asc()).all() if site_ids else []
    accounts = SftpAccount.query.filter_by(assigned_user_id=user.id, enabled=True).order_by(SftpAccount.username.asc()).all()
    payloads = [sftp_filezilla_payload(account) for account in accounts]
    personal_account = SftpAccount.query.filter_by(assigned_user_id=user.id).order_by(SftpAccount.id.asc()).first()
    sftp_self = {
        'host': sftp_connection_host(),
        'port': SFTP_LOCAL_PORT if SFTP_CONNECTION_MODE == 'cloudflare' else 22,
        'connection_mode': SFTP_CONNECTION_MODE,
        'tunnel_host': SFTP_TUNNEL_HOST,
        'enabled': bool(personal_account and personal_account.enabled),
        'username': personal_account.username if personal_account else user.username,
        'status': personal_account.system_state if personal_account else 'disabled',
        'has_sites': bool(site_ids),
    }
    return render_template(
        'user_sftp_access.html', user=user, sites=sites,
        sftp_connections=payloads, sftp_self=sftp_self,
        can_create_site=user_has_role_permission(user, 'site.create'),
    )


@app.route('/dashboard/sftp-access/toggle', methods=['POST'])
def user_sftp_toggle_access():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    if not user or user.is_banned:
        return abort(403)
    require_role_permission(user, 'sftp.access')

    desired_state = (request.form.get('desired_state') or '').strip().lower()
    if desired_state not in {'enable', 'disable'}:
        flash('Невірна дія SFTP.', 'error')
        return redirect(url_for('user_sftp_access'))

    account, error_message = get_or_prepare_personal_sftp_account(user)
    if error_message:
        flash(error_message, 'error')
        return redirect(url_for('user_sftp_access'))

    if desired_state == 'enable':
        account.enabled = True
        compatible_hash = normalize_sftp_password_hash(user.password)
        temporary_password = None
        if compatible_hash:
            account.password_hash = compatible_hash
        elif not account.password_hash:
            temporary_password = generate_temporary_password()
            account.password_hash = build_sftp_password_hash(temporary_password)
        account.auth_type = 'password'
        db.session.commit()
        queue_sftp_provision(account, 'create' if account.system_state in {'pending', ''} else 'toggle', actor=user.username)
        record_sftp_audit(account, 'self_service_enabled', status='success')
        log_action('sftp.self.enable', user.username)
        if temporary_password:
            flash(f'SFTP доступ увімкнено. Тимчасовий пароль (показується один раз): {temporary_password}', 'success')
        else:
            flash('SFTP доступ увімкнено. Використовуйте чинний SFTP пароль.', 'success')
    else:
        account.enabled = False
        db.session.commit()
        queue_sftp_provision(account, 'toggle', actor=user.username)
        record_sftp_audit(account, 'self_service_disabled', status='success')
        log_action('sftp.self.disable', user.username)
        flash('SFTP доступ вимкнено.', 'success')

    return redirect(url_for('user_sftp_access'))


@app.route('/dashboard/sftp-access/reset-password', methods=['POST'])
def user_sftp_reset_password():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    if not user or user.is_banned:
        abort(403)
    require_role_permission(user, 'sftp.access')
    account = SftpAccount.query.filter_by(assigned_user_id=user.id, enabled=True).order_by(SftpAccount.id.asc()).first()
    if not account:
        flash('Спочатку увімкніть SFTP доступ.', 'error')
        return redirect(url_for('user_sftp_access'))
    temporary_password = generate_temporary_password()
    password_hash = build_sftp_password_hash(temporary_password)
    if not password_hash:
        flash('Не вдалося створити SFTP пароль. Спробуйте ще раз.', 'error')
        return redirect(url_for('user_sftp_access'))
    account.password_hash = password_hash
    account.auth_type = 'password'
    db.session.commit()
    queue_sftp_provision(account, 'toggle', actor=user.username)
    record_sftp_audit(account, 'self_service_password_reset', status='success')
    log_action('sftp.self.password_reset', user.username)
    flash(f'Новий SFTP пароль (скопіюйте зараз — повторно він не показуватиметься): {temporary_password}', 'success')
    return redirect(url_for('user_sftp_access'))


@app.route('/developer/sftp-access')
@developer_required
def developer_sftp_access():
    user = db.session.get(User, session['user_id'])
    require_role_permission(user, 'sftp.access')
    site_ids = assigned_application_ids(user)
    sites = Site.query.filter(Site.id.in_(site_ids)).order_by(Site.name.asc()).all() if site_ids else []
    accounts = SftpAccount.query.filter(
        (SftpAccount.assigned_user_id == user.id) | (SftpAccount.role == 'developer')
    ).order_by(SftpAccount.username.asc()).all()
    payloads = [sftp_filezilla_payload(account) for account in accounts if (user_role(user) == 'admin' or account.assigned_user_id == user.id)]
    return render_template('developer_sftp_access.html', user=user, sites=sites, sftp_connections=payloads)


@app.route('/developer/sftp-users')
@admin_required
def developer_sftp_users():
    apply_sftp_provision_results()
    users = User.query.order_by(User.username.asc()).all()
    sites = Site.query.order_by(Site.name.asc()).all()
    accounts = SftpAccount.query.order_by(SftpAccount.username.asc()).all()
    audit_rows = SftpAuditEvent.query.order_by(SftpAuditEvent.id.desc()).limit(200).all()
    account_assignments = {account.id: set(parse_json_list(account.assigned_applications_json, int)) for account in accounts}
    return render_template('developer_sftp_users.html', users=users, sites=sites, accounts=accounts, audit_rows=audit_rows, account_assignments=account_assignments, system_accounts=system_sftp_inventory(accounts))


@app.route('/developer/sftp-users/create', methods=['POST'])
@admin_required
def developer_sftp_users_create():
    username = secure_filename((request.form.get('username') or '').strip().lower().replace('-', '_'))
    role = (request.form.get('role') or 'user').strip().lower()
    assigned_user_id = request.form.get('assigned_user_id', type=int)
    assigned_site_ids = [int(item) for item in request.form.getlist('assigned_site_ids') if item.isdigit()]
    auth_type = (request.form.get('auth_type') or 'password').strip().lower()
    chroot_directory = (request.form.get('chroot_directory') or '').strip()
    if not re.fullmatch(r'[a-z][a-z0-9_]{2,31}', username):
        flash('SFTP username: 3-32 символи, латиниця/цифри/_ і початок з літери.', 'error')
        return redirect(url_for('developer_sftp_users'))
    if role not in {'user', 'developer'}:
        flash('Підтримуються лише ролі user/developer.', 'error')
        return redirect(url_for('developer_sftp_users'))
    if auth_type not in {'password', 'key', 'both'}:
        flash('Невідомий тип автентифікації.', 'error')
        return redirect(url_for('developer_sftp_users'))
    if SftpAccount.query.filter_by(username=username).first():
        flash('SFTP account з таким username уже існує.', 'error')
        return redirect(url_for('developer_sftp_users'))
    assigned_user = db.session.get(User, assigned_user_id) if assigned_user_id else None
    if assigned_user_id and not assigned_user:
        flash('Призначений користувач не знайдений.', 'error')
        return redirect(url_for('developer_sftp_users'))
    if not chroot_directory:
        if assigned_site_ids:
            first_site = db.session.get(Site, assigned_site_ids[0])
            if first_site:
                chroot_directory = os.path.join(app.config['UPLOAD_FOLDER'], first_site.folder_name)
        if not chroot_directory:
            chroot_directory = os.path.join('/srv', 'apps', username)
    temp_password = generate_temporary_password() if auth_type in {'password', 'both'} else ''
    account = SftpAccount(
        username=username,
        role=role,
        assigned_user_id=assigned_user.id if assigned_user else None,
        assigned_applications_json=dump_json_list(assigned_site_ids),
        chroot_directory=chroot_directory,
        auth_type=auth_type,
        password_hash=build_sftp_password_hash(temp_password) if temp_password else None,
        public_keys_json='[]',
        enabled=True,
        system_state='pending',
    )
    db.session.add(account)
    db.session.commit()
    queue_sftp_provision(account, 'create', actor=session.get('username', 'admin'))
    record_sftp_audit(account, 'account_created', status='success', detail=f'role={role}; assigned_apps={len(assigned_site_ids)}')
    log_action('sftp.account.create', f'{username} ({role})')
    if temp_password:
        flash(f'SFTP account створено: {username}. Тимчасовий пароль: {temp_password}', 'success')
    else:
        flash(f'SFTP account створено: {username}. Додайте SSH key перед використанням.', 'success')
    return redirect(url_for('developer_sftp_users'))


@app.route('/developer/sftp-users/<int:account_id>/toggle', methods=['POST'])
@admin_required
def developer_sftp_users_toggle(account_id):
    account = db.session.get(SftpAccount, account_id)
    if not account:
        abort(404)
    account.enabled = not account.enabled
    db.session.commit()
    queue_sftp_provision(account, 'toggle', actor=session.get('username', 'admin'))
    record_sftp_audit(account, 'account_enabled' if account.enabled else 'account_disabled', status='success')
    log_action('sftp.account.toggle', f'{account.username}: enabled={account.enabled}')
    return redirect(url_for('developer_sftp_users'))


@app.route('/developer/sftp-users/<int:account_id>/reset-password', methods=['POST'])
@admin_required
def developer_sftp_users_reset_password(account_id):
    account = db.session.get(SftpAccount, account_id)
    if not account:
        abort(404)
    temporary_password = generate_temporary_password()
    account.password_hash = build_sftp_password_hash(temporary_password)
    if account.auth_type == 'key':
        account.auth_type = 'both'
    db.session.commit()
    queue_sftp_provision(account, 'reset-credentials', actor=session.get('username', 'admin'))
    record_sftp_audit(account, 'credentials_reset', status='success')
    log_action('sftp.account.reset_password', account.username)
    flash(f'Новий тимчасовий пароль для {account.username}: {temporary_password}', 'success')
    return redirect(url_for('developer_sftp_users'))


@app.route('/developer/sftp-users/<int:account_id>/add-key', methods=['POST'])
@admin_required
def developer_sftp_users_add_key(account_id):
    account = db.session.get(SftpAccount, account_id)
    if not account:
        abort(404)
    public_key = (request.form.get('public_key') or '').strip()
    if not re.fullmatch(r'(ssh-rsa|ssh-ed25519|ecdsa-sha2-nistp\d+)\s+[A-Za-z0-9+/=]+(?:\s+.+)?', public_key):
        flash('Некоректний SSH public key формат.', 'error')
        return redirect(url_for('developer_sftp_users'))
    keys = parse_json_list(account.public_keys_json, str)
    if public_key not in keys:
        keys.append(public_key)
    account.public_keys_json = dump_json_list(keys)
    if account.auth_type == 'password':
        account.auth_type = 'both'
    db.session.commit()
    queue_sftp_provision(account, 'add-key', actor=session.get('username', 'admin'))
    record_sftp_audit(account, 'ssh_key_added', status='success')
    log_action('sftp.account.add_key', account.username)
    flash(f'SSH ключ додано для {account.username}.', 'success')
    return redirect(url_for('developer_sftp_users'))


@app.route('/developer/sftp-users/<int:account_id>/assign', methods=['POST'])
@admin_required
def developer_sftp_users_assign(account_id):
    account = db.session.get(SftpAccount, account_id)
    if not account:
        abort(404)
    assigned_user_id = request.form.get('assigned_user_id', type=int)
    site_ids = [int(item) for item in request.form.getlist('assigned_site_ids') if item.isdigit()]
    assigned_user = db.session.get(User, assigned_user_id) if assigned_user_id else None
    account.assigned_user_id = assigned_user.id if assigned_user else None
    account.assigned_applications_json = dump_json_list(site_ids)
    if site_ids:
        account.chroot_directory = os.path.join('/srv/apps', account.username)
    db.session.commit()
    queue_sftp_provision(account, 'assign', actor=session.get('username', 'admin'))
    record_sftp_audit(account, 'assignments_updated', status='success', detail=f'apps={len(site_ids)}')
    log_action('sftp.account.assign', f'{account.username}: apps={len(site_ids)}')
    return redirect(url_for('developer_sftp_users'))


@app.route('/developer/sftp-users/provisioner/run', methods=['POST'])
@admin_required
def developer_sftp_run_provisioner():
    code = 1
    output = 'not started'
    try:
        result = subprocess.run(['sudo', '-n', 'systemctl', 'start', SFTP_PROVISION_SERVICE], capture_output=True, text=True, timeout=10)
        code = result.returncode
        output = (result.stdout or result.stderr or '').strip()
    except Exception as exc:
        output = str(exc)
    if code == 0:
        flash('SFTP provisioner started.', 'success')
    else:
        flash(f'SFTP provisioner запуск не вдався: {output[:240] or "permission denied"}', 'error')
    log_action('sftp.provisioner.run', f'code={code}; {output[:120]}')
    return redirect(url_for('developer_sftp_users'))


@app.route('/developer/upload-history')
@developer_required
def developer_upload_history():
    user = db.session.get(User, session['user_id'])
    role = user_role(user)
    if role == 'admin':
        rows = UploadHistory.query.order_by(UploadHistory.id.desc()).limit(300).all()
    else:
        site_ids = assigned_application_ids(user)
        if not site_ids:
            rows = []
        else:
            rows = UploadHistory.query.filter(UploadHistory.application_id.in_(site_ids)).order_by(UploadHistory.id.desc()).limit(300).all()
    return render_template('developer_upload_history.html', rows=rows)


@app.route('/developer/site/<int:site_id>/backup', methods=['POST'])
@admin_required
def developer_site_backup(site_id):
    site = db.session.get(Site, site_id)
    if not site:
        abort(404)
    access = ensure_application_access(site)
    try:
        create_backup_archive(site, access=access)
    except ValueError as exc:
        flash(str(exc), 'error')
        return redirect(url_for('dashboard'))
    backups = list_site_backups(site, access=access)
    for old in backups[10:]:
        os.remove(os.path.join(backup_directory(site, access=access), old['name']))
    log_action('backup.create', f'{site.name} (developer)')
    flash(f'Резервну копію для {site.name} створено.', 'success')
    return redirect(url_for('dashboard'))


@app.route('/site/<int:site_id>/backup/queue', methods=['POST'])
def queue_site_backup(site_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    site = db.session.get(Site, site_id)
    require_application_permission(user, site, 'backup.create')
    job = create_job('site.backup', target=site.name, payload={'site_id': site.id}, created_by=user.username)
    log_action('backup.queue', f'#{job.id} {site.name}')
    record_upload_history(site, user, f'{site.name}.backup.queue', 0, source='panel', status='success', deployment='queued', detail=f'job={job.id}')
    flash(f'{translate("backup_queued")} #{job.id}.', 'success')
    return redirect(url_for('manage_site', folder_name=site.folder_name))


@app.route('/site/<int:site_id>/backup/<backup_name>/restore/queue', methods=['POST'])
def queue_restore_site_backup(site_id, backup_name):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    site = db.session.get(Site, site_id)
    require_application_permission(user, site, 'backup.restore')
    get_backup_path(site, backup_name)
    job = create_job('site.restore', target=f'{site.name}:{backup_name}', payload={'site_id': site.id, 'backup_name': backup_name}, created_by=user.username)
    log_action('backup.restore.queue', f'#{job.id} {site.name}:{backup_name}')
    record_upload_history(site, user, backup_name, 0, source='panel', status='success', deployment='restore-queued', detail=f'job={job.id}')
    flash(f'{translate("restore_queued")} #{job.id}.', 'success')
    return redirect(url_for('manage_site', folder_name=site.folder_name))

# -----------------------------------------------

@app.route('/site/<folder_name>', methods=['GET', 'POST'])
def manage_site(folder_name):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user = db.session.get(User, session['user_id'])
    site = Site.query.filter_by(folder_name=folder_name).first()
    if not user or not site:
        return abort(404)
    access = ensure_application_access(site)
    if request.method == 'GET':
        require_application_permission(user, site, 'files.view')
    site_path = application_root(access, bucket='file')
    os.makedirs(site_path, exist_ok=True)

    current_dir = normalized_relative_path(request.values.get('dir', ''))
    if current_dir:
        enforce_wordpress_path_permission(user, access, site_path, current_dir)
        current_abs_path = safe_resource_path(access, current_dir, bucket='file')
        if not os.path.isdir(current_abs_path):
            abort(404)
    else:
        current_abs_path = site_path

    def redirect_manage_with_dir(dir_value):
        safe_dir = normalized_relative_path(dir_value or '')
        if safe_dir:
            return redirect(url_for('manage_site', folder_name=folder_name, dir=safe_dir))
        return redirect(url_for('manage_site', folder_name=folder_name))
    
    if request.method == 'POST':
        action = request.form.get('action', 'upload')
        current_dir_form = normalized_relative_path(request.form.get('current_dir', current_dir))
        if action == 'mkdir':
            require_application_permission(user, site, 'files.create')
            folder_input = request.form.get('folder_name', '').strip().replace('\\', '/')
            folder = normalized_relative_path(os.path.join(current_dir_form, folder_input)) if folder_input else ''
            if not folder or any(part in {'', '.', '..'} for part in folder.split('/')):
                flash(translate('invalid_directory_name'), 'error')
            else:
                enforce_wordpress_path_permission(user, access, site_path, folder)
                os.makedirs(safe_resource_path(access, folder, bucket='file'), exist_ok=True)
                record_upload_history(site, user, folder, 0, source='panel', status='success', deployment='', detail='mkdir')
                flash(translate('directory_created'), 'success')
        elif action == 'save':
            require_application_permission(user, site, 'files.upload')
            relative = request.form.get('file_path', '')
            enforce_wordpress_path_permission(user, access, site_path, relative)
            target = safe_resource_path(access, relative, bucket='file')
            content = request.form.get('content', '')
            if len(content.encode('utf-8')) > 1024 * 1024 or not os.path.isfile(target):
                abort(400)
            existing_size = os.path.getsize(target)
            new_size = len(content.encode('utf-8'))
            app_quota_bytes = max(64, int(access.application_quota_mb or 64)) * 1024 * 1024
            projected = max(0, application_usage_bytes_for_access(access) - existing_size + new_size)
            if projected > app_quota_bytes:
                flash('Перевищено квоту застосунку.', 'error')
                record_upload_history(site, user, relative, new_size, source='panel', status='blocked', deployment='', detail='application-quota-limit-save')
                return redirect_manage_with_dir(current_dir_form)
            with open(target, 'w', encoding='utf-8', newline='') as handle:
                handle.write(content)
            flash(translate('file_saved'), 'success')
            log_action('file.edit', f'{site.name}/{relative}')
            record_upload_history(site, user, relative, len(content.encode('utf-8')), source='panel', status='success', deployment='', detail='save')
        elif action == 'rename':
            require_application_permission(user, site, 'files.rename')
            relative = request.form.get('file_path', '')
            new_name = secure_filename(request.form.get('new_name', ''))
            enforce_wordpress_path_permission(user, access, site_path, relative)
            source = safe_resource_path(access, relative, bucket='file')
            if not new_name or not os.path.exists(source):
                abort(400)
            destination = safe_resource_path(access, os.path.join(os.path.dirname(relative), new_name), bucket='file')
            enforce_wordpress_path_permission(user, access, site_path, os.path.join(os.path.dirname(relative), new_name))
            if os.path.exists(destination):
                flash(translate('file_exists'), 'error')
            else:
                os.rename(source, destination)
                log_action('file.rename', f'{site.name}/{relative} → {new_name}')
                record_upload_history(site, user, relative, 0, source='panel', status='success', deployment='', detail=f'rename:{new_name}')
                flash('Перейменовано.', 'success')
        elif action == 'extract':
            require_application_permission(user, site, 'files.extract')
            relative = normalized_relative_path(request.form.get('file_path', ''))
            source = safe_resource_path(access, relative, bucket='file')
            if not relative.lower().endswith('.zip') or not os.path.isfile(source):
                abort(400)
            target_relative = normalized_relative_path(os.path.dirname(relative))
            target = safe_resource_path(access, target_relative, bucket='file')
            with zipfile.ZipFile(source, 'r') as archive:
                members = archive.infolist()
                prefix = zip_single_root_folder(members) if request.form.get('flatten_root') == '1' else ''
                safe_extract_zip(archive, target, strip_prefix=prefix or '')
            log_action('file.extract', f'{site.name}/{relative}; flatten={bool(prefix)}')
            record_upload_history(site, user, relative, os.path.getsize(source), source='panel', status='success', deployment='', detail='explicit-safe-extract')
            flash('Архів безпечно розпаковано.', 'success')
        elif action in {'move', 'copy'}:
            permission = 'files.rename' if action == 'move' else 'files.create'
            require_application_permission(user, site, permission)
            relative = normalized_relative_path(request.form.get('file_path', ''))
            destination_relative = normalized_relative_path(request.form.get('destination', ''))
            source = safe_resource_path(access, relative, bucket='file')
            destination = safe_resource_path(access, destination_relative, bucket='file')
            enforce_wordpress_path_permission(user, access, site_path, relative)
            enforce_wordpress_path_permission(user, access, site_path, destination_relative)
            if not relative or not destination_relative or not os.path.isfile(source) or os.path.exists(destination):
                abort(400)
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            if action == 'copy':
                enforce_application_quota(access, application_usage_bytes_for_access(access) + os.path.getsize(source))
                shutil.copy2(source, destination)
            else:
                shutil.move(source, destination)
            log_action(f'file.{action}', f'{site.name}/{relative} → {destination_relative}')
            record_upload_history(site, user, relative, os.path.getsize(destination), source='panel', status='success', deployment='', detail=f'{action}:{destination_relative}')
            flash('Файл скопійовано.' if action == 'copy' else 'Файл переміщено.', 'success')
        else:
            require_application_permission(user, site, 'files.upload')
            target_dir_relative = normalized_relative_path(request.form.get('target_dir', current_dir_form))
            enforce_wordpress_path_permission(user, access, site_path, target_dir_relative)
            target_dir = safe_resource_path(access, target_dir_relative, bucket='upload')
            os.makedirs(target_dir, exist_ok=True)
            uploaded = 0
            owner_quota_bytes = site.owner.quota_mb * 1024 * 1024
            app_quota_bytes = max(64, access.storage_quota_mb) * 1024 * 1024
            quota_bytes = min(owner_quota_bytes, app_quota_bytes)
            application_quota_bytes = max(64, int(access.application_quota_mb or 64)) * 1024 * 1024
            max_file_size = max(1, access.max_file_size_mb) * 1024 * 1024
            max_upload_size = max(access.max_file_size_mb, access.max_upload_size_mb) * 1024 * 1024
            uploaded_total = 0
            application_usage_bytes = application_usage_bytes_for_access(access)
            for file in request.files.getlist('files'):
                if not file or not file.filename:
                    continue
                filename = secure_filename(os.path.basename(file.filename))
                if not filename:
                    continue
                file.stream.seek(0, os.SEEK_END)
                upload_size = file.stream.tell()
                file.stream.seek(0)
                if upload_size > max_file_size:
                    flash(f'{filename}: перевищено ліміт одного файлу.', 'error')
                    record_upload_history(site, user, filename, upload_size, source='panel', status='blocked', deployment='', detail='file-size-limit')
                    continue
                if uploaded_total + upload_size > max_upload_size:
                    flash(f'{filename}: перевищено сумарний ліміт завантаження.', 'error')
                    record_upload_history(site, user, filename, upload_size, source='panel', status='blocked', deployment='', detail='upload-size-limit')
                    continue
                if user_usage_bytes(site.owner) + upload_size > quota_bytes:
                    flash(f'{filename}: недостатньо доступної квоти.', 'error')
                    record_upload_history(site, user, filename, upload_size, source='panel', status='blocked', deployment='', detail='quota-limit')
                    continue
                if application_usage_bytes + upload_size > application_quota_bytes:
                    flash(f'{filename}: перевищено квоту застосунку.', 'error')
                    record_upload_history(site, user, filename, upload_size, source='panel', status='blocked', deployment='', detail='application-quota-limit')
                    continue
                file_path = os.path.join(target_dir, filename)
                file.save(file_path)
                uploaded_total += upload_size
                application_usage_bytes += upload_size
                if filename.lower().endswith('.zip') and request.form.get('extract_zip') == '1':
                    try:
                        with zipfile.ZipFile(file_path, 'r') as zip_ref:
                            members = zip_ref.infolist()
                            extracted_size = sum(item.file_size for item in members)
                            if len(members) > 10000 or extracted_size > 256 * 1024 * 1024:
                                raise ValueError('Архів перевищує безпечний ліміт')
                            if user_usage_bytes(site.owner) - upload_size + extracted_size > quota_bytes:
                                raise ValueError('Розпакований архів перевищить квоту користувача')
                            if application_usage_bytes - upload_size + extracted_size > application_quota_bytes:
                                raise ValueError('Розпакований архів перевищить квоту застосунку')
                            strip_prefix = zip_single_root_folder(members) if not target_dir_relative else None
                            strip_with_sep = f'{strip_prefix}/' if strip_prefix else ''
                            for member in members:
                                member_name = (member.filename or '').replace('\\', '/')
                                if strip_prefix:
                                    normalized_member = os.path.normpath(member_name).replace('\\', '/')
                                    if normalized_member == strip_prefix:
                                        continue
                                    if not normalized_member.startswith(strip_with_sep):
                                        raise ValueError('Архів має некоректну структуру для clean mode')
                                    member_name = normalized_member[len(strip_with_sep):]
                                    if not member_name:
                                        continue
                                member_rel = normalized_relative_path(os.path.join(target_dir_relative, member_name))
                                enforce_wordpress_path_permission(user, access, site_path, member_rel)
                            safe_extract_zip(zip_ref, target_dir, strip_prefix=strip_prefix or '')
                        application_usage_bytes = application_usage_bytes_for_access(access)
                        detail = 'zip-upload+extract-clean' if strip_prefix else 'zip-upload+extract'
                        record_upload_history(site, user, filename, upload_size, source='panel', status='success', deployment='', detail=detail)
                    except (zipfile.BadZipFile, ValueError) as exc:
                        flash(str(exc), 'error')
                        application_usage_bytes = max(0, application_usage_bytes - upload_size)
                        record_upload_history(site, user, filename, upload_size, source='panel', status='failed', deployment='', detail=str(exc))
                    finally:
                        os.remove(file_path)
                else:
                    record_upload_history(site, user, filename, upload_size, source='panel', status='success', deployment='', detail='upload')
                uploaded += 1
            if uploaded:
                log_action('file.upload', f'{site.name}: {uploaded} файлів')
                flash(f'{translate("files_uploaded")} {uploaded}.', 'success')
        return redirect_manage_with_dir(current_dir_form)
        
    files = []
    directories = []
    try:
        entries = sorted(
            os.scandir(current_abs_path),
            key=lambda item: (not item.is_dir(follow_symlinks=False), item.name.lower())
        )
    except OSError:
        entries = []
    for entry in entries:
        rel_path = normalized_relative_path(os.path.join(current_dir, entry.name))
        if entry.is_dir(follow_symlinks=False):
            directories.append({'name': entry.name, 'path': rel_path})
        elif entry.is_file(follow_symlinks=False):
            files.append({'name': entry.name, 'path': rel_path, 'size': entry.stat().st_size})

    parent_dir = normalized_relative_path(os.path.dirname(current_dir)) if current_dir else ''

    edit_path = request.args.get('edit', '')
    edit_content = None
    if edit_path:
        enforce_wordpress_path_permission(user, access, site_path, edit_path)
        target = safe_resource_path(access, edit_path, bucket='file')
        if os.path.isfile(target) and os.path.getsize(target) <= 1024 * 1024 and os.path.splitext(target)[1].lower() in TEXT_EXTENSIONS:
            try:
                with open(target, 'r', encoding='utf-8') as handle:
                    edit_content = handle.read()
            except UnicodeDecodeError:
                flash('Цей файл не є текстовим.', 'error')

    permissions = user_permissions_for_application(user, access)
    public_domain = site.custom_domain or f'{site.name}.myh.guru'
    domain_state = probe_domain_status(public_domain)
    latest_deployment = DeploymentEvent.query.filter_by(site_name=site.name).order_by(DeploymentEvent.created_at.desc()).first()
    wordpress_version = None
    wordpress_state = {'code': 'not_wordpress', 'files': False, 'config': False, 'database': False, 'nested': False}
    if site.application_type == 'wordpress':
        wordpress_state = detect_wordpress_state(site, access)
        version_file = os.path.join(site_path, 'wp-includes', 'version.php')
        try:
            with open(version_file, encoding='utf-8') as wordpress_version_file:
                version_match = re.search(r"\$wp_version\s*=\s*'([^']+)'", wordpress_version_file.read())
            wordpress_version = version_match.group(1) if version_match else None
        except OSError:
            pass
    wordpress_cron = None
    if site.application_type == 'wordpress':
        wordpress_cron = JobTask.query.filter_by(job_type='wordpress.cron', target=str(site.id)).order_by(JobTask.created_at.desc()).first()
    return render_template(
        'manage_site.html',
        site=site,
        access=access,
        files=files,
        directories=directories,
        current_dir=current_dir,
        parent_dir=parent_dir,
        edit_path=edit_path,
        edit_content=edit_content,
        backups=list_site_backups(site, access=access),
        usage_bytes=user_usage_bytes(site.owner),
        quota=quota_snapshot(access),
        permissions=permissions,
        runtime_metrics=application_runtime_metrics(access),
        actual_status=actual_site_runtime_status(site),
        domain_state=domain_state,
        latest_deployment=latest_deployment,
        wordpress_version=wordpress_version,
        wordpress_state=wordpress_state,
        wordpress_cron=wordpress_cron,
    )


@app.route('/site/<int:site_id>/files/download')
def download_site_file(site_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    site = db.session.get(Site, site_id)
    access = require_application_permission(user, site, 'files.download')
    relative = normalized_relative_path(request.args.get('path', ''))
    target = safe_resource_path(access, relative, bucket='file')
    if not relative or not os.path.isfile(target):
        abort(404)
    return send_file(target, as_attachment=True, download_name=os.path.basename(target))

@app.route('/view-site/<folder_name>/', defaults={'subpath': 'index.html'})
@app.route('/view-site/<folder_name>/<path:subpath>')
def view_site(folder_name, subpath):
    site = Site.query.filter_by(folder_name=folder_name).first()
    if not site or site.is_banned or (site.owner and site.owner.is_banned):
        return abort(404)

    access = ensure_application_access(site)
    site_path = application_root(access, bucket='file')
    if subpath in {'', 'index.html'}:
        index_html = os.path.join(site_path, 'index.html')
        index_php = os.path.join(site_path, 'index.php')
        if not os.path.exists(index_html):
            if os.path.exists(index_php):
                subpath = 'index.php'
            else:
                try:
                    with os.scandir(site_path) as entries:
                        has_existing_content = next(entries, None) is not None
                except OSError:
                    has_existing_content = False
                if not has_existing_content:
                    os.makedirs(site_path, exist_ok=True)
                    scaffold_site_content(site_path, site.name)
    return send_from_directory(site_path, subpath)


@app.route('/site/<int:site_id>/backup', methods=['POST'])
def create_site_backup(site_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    site = db.session.get(Site, site_id)
    require_application_permission(user, site, 'backup.create')
    access = ensure_application_access(site)
    try:
        create_backup_archive(site, access=access)
    except ValueError as exc:
        flash(str(exc), 'error')
        return redirect(url_for('manage_site', folder_name=site.folder_name))
    backups = list_site_backups(site, access=access)
    for old in backups[10:]:
        os.remove(os.path.join(backup_directory(site, access=access), old['name']))
    log_action('backup.create', site.name)
    record_upload_history(site, user, f'{site.name}.backup.zip', 0, source='panel', status='success', deployment='backup', detail='manual backup')
    flash(translate('backup_created'), 'success')
    return redirect(url_for('manage_site', folder_name=site.folder_name))


def get_backup_path(site, backup_name):
    if not re.fullmatch(r'\d{8}-\d{6}\.zip', backup_name):
        abort(400)
    path = safe_site_path(backup_directory(site, access=ensure_application_access(site)), backup_name)
    if not os.path.isfile(path):
        abort(404)
    return path


@app.route('/site/<int:site_id>/backup/<backup_name>/download')
def download_site_backup(site_id, backup_name):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    site = db.session.get(Site, site_id)
    require_application_permission(user, site, 'backup.download')
    return send_file(get_backup_path(site, backup_name), as_attachment=True, download_name=f'{site.name}-{backup_name}')


@app.route('/site/<int:site_id>/backup/<backup_name>/delete', methods=['POST'])
def delete_site_backup(site_id, backup_name):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    site = db.session.get(Site, site_id)
    require_application_permission(user, site, 'backup.restore')
    archive_path = get_backup_path(site, backup_name)
    archive_size = os.path.getsize(archive_path)
    os.remove(archive_path)
    for sidecar in (archive_path + '.wordpress.sql', archive_path + '.wordpress.sql.sha256'):
        if os.path.isfile(sidecar):
            os.remove(sidecar)
    log_action('backup.delete', f'{site.name}: {backup_name}')
    record_upload_history(site, user, backup_name, archive_size, source='panel', status='success', deployment='backup-delete', detail='delete backup')
    flash('Резервну копію видалено.', 'success')
    return redirect(url_for('manage_site', folder_name=site.folder_name))


@app.route('/site/<int:site_id>/backup/<backup_name>/restore', methods=['POST'])
def restore_site_backup(site_id, backup_name):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    site = db.session.get(Site, site_id)
    require_application_permission(user, site, 'backup.restore')
    archive_path = get_backup_path(site, backup_name)
    access = ensure_application_access(site)
    site_path = application_root(access, bucket='file')
    try:
        restore_size = estimate_zip_unpacked_bytes(archive_path)
        enforce_application_quota(access, restore_size)
        validate_wordpress_database_sidecar(site, archive_path)
    except ValueError as exc:
        flash(str(exc), 'error')
        record_upload_history(site, user, backup_name, os.path.getsize(archive_path), source='panel', status='blocked', deployment='restore', detail='application-quota-limit-restore')
        return redirect(url_for('manage_site', folder_name=site.folder_name))
    for root, directories, filenames in os.walk(site_path, topdown=False):
        for filename in filenames:
            os.remove(os.path.join(root, filename))
        for directory in directories:
            os.rmdir(os.path.join(root, directory))
    with zipfile.ZipFile(archive_path, 'r') as archive:
        safe_extract_zip(archive, site_path)
    reconcile_wordpress_permissions(site, access)
    restore_wordpress_database_sidecar(site, archive_path)
    log_action('backup.restore', f'{site.name}: {backup_name}')
    record_upload_history(site, user, backup_name, os.path.getsize(archive_path), source='panel', status='success', deployment='restore', detail='restore from backup')
    flash(translate('restore_completed'), 'success')
    return redirect(url_for('manage_site', folder_name=site.folder_name))


@app.route('/site/<int:site_id>/domain', methods=['POST'])
def set_custom_domain(site_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    site = db.session.get(Site, site_id)
    require_application_permission(user, site, 'domain.manage')
    domain = request.form.get('custom_domain', '').strip().lower().rstrip('.')
    if domain and not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', domain):
        flash(translate('invalid_domain'), 'error')
    elif domain and Site.query.filter(Site.custom_domain == domain, Site.id != site.id).first():
        flash(translate('domain_taken'), 'error')
    else:
        old_domain = site.custom_domain
        if domain:
            success, detail = ensure_cloudflare_domain_record(domain)
            if not success:
                flash(f'DNS/SSL activation failed: {detail[:300]}', 'error')
                return redirect(url_for('manage_site', folder_name=site.folder_name))
        if old_domain and old_domain != domain:
            ensure_cloudflare_domain_record(old_domain, remove=True)
        site.custom_domain = domain or None
        db.session.commit()
        log_action('domain.update', f'{site.name}: {domain or "removed"}')
        flash(translate('domain_saved'), 'success')
    return redirect(url_for('manage_site', folder_name=site.folder_name))

@app.route('/delete-site/<int:site_id>', methods=['POST'])
def delete_site(site_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user = db.session.get(User, session['user_id'])
    site = Site.query.get_or_404(site_id)
    require_application_permission(user, site, 'site.manage')

    access = ensure_application_access(site)
    deployment_root = access.deployment_root
    backup_root = access.backup_root
    # Detach the application from every SFTP chroot before removing its source.
    for account in SftpAccount.query.all():
        assigned = parse_json_list(account.assigned_applications_json)
        remaining = [item for item in assigned if str(item) != str(site.id)]
        if remaining != assigned:
            account.assigned_applications_json = dump_json_list(remaining)
            queue_sftp_provision(account, action='assign')
    db.session.commit()
    try:
        subprocess.run(['sudo', 'systemctl', 'start', 'myh-sftp-provision.service'],
                       capture_output=True, text=True, timeout=45, check=False)
    except (OSError, subprocess.SubprocessError):
        pass
    if access.deployment_root and os.path.isfile(os.path.join(access.deployment_root, 'runtime.json')):
        try:
            compose_action(access.deployment_root, 'delete')
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    site_path = application_root(access, bucket='file')
    if os.path.exists(site_path):
        try:
            remove_tree(site_path)
        except PermissionError:
            # WordPress owns generated files as its unprivileged container UID.
            subprocess.run(['docker', 'run', '--rm', '--network', 'none', '-v', f'{site_path}:/target',
                            'alpine:3.22', 'find', '/target', '-mindepth', '1', '-delete'],
                           capture_output=True, text=True, timeout=90, check=False)
            remove_tree(site_path)
    site_container_root = os.path.join(app.config['UPLOAD_FOLDER'], site.folder_name)
    if os.path.isdir(site_container_root) and not os.listdir(site_container_root):
        os.rmdir(site_container_root)
        
    site_name = site.name
    for resource in DatabaseResource.query.filter_by(application_id=site.id).all():
        deprovision_mysql_database(resource.database_name, resource.database_user)
        db.session.delete(resource)
    for variable in EnvironmentVariable.query.filter_by(application_id=site.id).all():
        db.session.delete(variable)
    for integration in Integration.query.filter_by(application_id=site.id).all():
        db.session.delete(integration)
    access = ApplicationAccess.query.filter_by(site_id=site.id).first()
    if access:
        db.session.delete(access)
    db.session.delete(site)
    db.session.commit()
    if deployment_root and os.path.isdir(deployment_root):
        remove_tree(deployment_root)
    if backup_root and os.path.isdir(backup_root):
        remove_tree(backup_root)
    secret_root = os.path.join(app.instance_path, 'application_secrets', str(site_id))
    if os.path.isdir(secret_root):
        remove_tree(secret_root)
    log_action('site.delete', f'{site_name}; files, databases, secrets and backups removed')
    
    return redirect(url_for('dashboard'))


def repair_missing_runtime_configuration(site, access):
    """Recreate a failed dynamic runtime without touching files, databases or secrets."""
    if site.runtime_type not in {'node', 'python', 'php', 'wordpress', 'docker'}:
        return False, 'Static and unknown sites do not have a managed runtime.'
    if site.application_type == 'wordpress':
        canonical_root = os.path.join(app.config['UPLOAD_FOLDER'], site.folder_name, 'public_html')
        os.makedirs(canonical_root, exist_ok=True)
        access.file_root = canonical_root; access.upload_root = canonical_root
    site_path = application_root(access, bucket='file')
    stack_root = os.path.join(APP_STACKS_ROOT, site.folder_name)
    port = site.internal_port or allocate_application_port()
    try:
        if site.application_type == 'wordpress':
            populate = site.installation_mode != 'manual'
            metadata = prepare_wordpress_runtime(stack_root, site_path, port, populate_wordpress=populate)
        else:
            inferred = infer_runtime_commands(site_path, site.runtime_type)
            install_command = site.install_command or inferred.get('install_command', '')
            build_command = site.build_command or inferred.get('build_command', '')
            start_command = site.start_command or inferred.get('start_command', '')
            metadata = prepare_runtime(
                stack_root, site_path, site.runtime_type, site.runtime_version, port,
                install_command=install_command, build_command=build_command,
                start_command=start_command, spa_enabled=site.spa_enabled,
            )
            site.install_command = install_command
            site.build_command = build_command
            site.start_command = start_command
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        return False, mask_sensitive_text(str(exc))[:300]
    access.deployment_root = stack_root
    site.internal_port = metadata['port']
    site.runtime_status = 'configured'
    db.session.commit()
    sync_runtime_environment(site)
    log_action('site.runtime.configuration.repaired', f'{site.name}:{site.runtime_type}:{metadata["port"]}')
    return True, 'repaired'


@app.route('/site/<int:site_id>/wordpress/retry-provisioning', methods=['POST'])
def retry_wordpress_provisioning(site_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id']); site = db.session.get(Site, site_id)
    require_application_permission(user, site, 'site.manage')
    if site.application_type != 'wordpress':
        abort(404)
    access = ensure_application_access(site)
    site.provisioning_phase = 'provisioning_runtime'; site.runtime_status = 'configuring'; db.session.commit()
    repaired, detail = repair_missing_runtime_configuration(site, access)
    if not repaired:
        site.provisioning_phase = 'failed_runtime'; site.runtime_status = 'error'; site.deployment_status = 'failed'; db.session.commit()
        flash('Не вдалося запустити PHP-середовище. Перегляньте runtime logs.', 'error')
        return redirect(url_for('manage_site', folder_name=site.folder_name) + '#wordpress-setup')
    code, output = compose_action(access.deployment_root, 'start')
    with open(os.path.join(access.deployment_root, 'runtime.json'), encoding='utf-8') as handle:
        metadata = json.load(handle)
    checked = healthcheck(metadata, timeout=45) if code == 0 else {'ok': False}
    if not checked.get('ok'):
        site.provisioning_phase = 'failed_healthcheck'; site.runtime_status = 'error'; site.deployment_status = 'failed'; db.session.commit()
        log_action('wordpress.provision.retry.failed', f'site={site.id}; phase=healthcheck; {mask_sensitive_text(output[-200:])}')
        flash('PHP-середовище створено, але web health check не пройдено.', 'error')
    else:
        state = detect_wordpress_state(site, access)
        if site.installation_mode == 'automatic' and state.get('code') != 'installed':
            try:
                install_wordpress_one_click(
                    site, access, (request.form.get('wordpress_title') or site.name).strip(),
                    (request.form.get('wordpress_admin') or '').strip(),
                    (request.form.get('wordpress_email') or '').strip(),
                    request.form.get('wordpress_password') or '',
                    (request.form.get('wordpress_language') or 'uk').strip(),
                )
                state = detect_wordpress_state(site, access)
            except (RuntimeError, ValueError, subprocess.SubprocessError):
                site.provisioning_phase = 'failed_install'; site.runtime_status = 'error'; site.deployment_status = 'failed'; db.session.commit()
                log_action('wordpress.provision.retry.failed', f'site={site.id}; phase=install')
                flash('Автоматичне встановлення не вдалося. Перевірте параметри адміністратора та runtime logs.', 'error')
                return redirect(url_for('manage_site', folder_name=site.folder_name) + '#wordpress-setup')
        site.runtime_status = 'running'; site.deployment_status = 'success'; site.last_restart_at = datetime.now()
        site.provisioning_phase = 'ready' if state.get('code') == 'installed' else ('failed_install' if site.installation_mode == 'automatic' else 'needs_setup'); db.session.commit()
        log_action('wordpress.provision.retry.success', f'site={site.id}; state={state.get("code")}')
        flash('WordPress infrastructure reconciled without duplicating site or database resources.', 'success')
    return redirect(url_for('manage_site', folder_name=site.folder_name) + '#wordpress-setup')


@app.route('/site/<int:site_id>/runtime/<action>', methods=['POST'])
def site_runtime_action(site_id, action):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    site = db.session.get(Site, site_id)
    require_application_permission(user, site, 'site.manage')
    if action not in {'start', 'stop', 'restart'}:
        abort(404)
    access = ensure_application_access(site)
    if not os.path.isfile(os.path.join(access.deployment_root, 'runtime.json')):
        repaired, detail = repair_missing_runtime_configuration(site, access)
        if not repaired:
            site.runtime_status = 'error'
            site.deployment_status = 'failed'
            db.session.commit()
            log_action('site.runtime.configuration.repair.failed', f'{site.name}:{detail}')
            flash('Runtime configuration could not be repaired. Check application logs.', 'error')
            return redirect(url_for('manage_site', folder_name=site.folder_name))
    code, output = compose_action(access.deployment_root, action)
    if code != 0:
        site.runtime_status = 'error'
        db.session.commit()
        log_action(f'site.runtime.{action}.failed', f'{site.name}:{output[-500:]}')
        flash('Runtime operation failed. Check application logs.', 'error')
        return redirect(url_for('manage_site', folder_name=site.folder_name))
    if action == 'stop':
        site.runtime_status = 'stopped'
    else:
        with open(os.path.join(access.deployment_root, 'runtime.json'), encoding='utf-8') as handle:
            metadata = json.load(handle)
        health_timeout = 90 if site.runtime_type in {'node', 'python'} else 30
        check = healthcheck(metadata, timeout=health_timeout)
        site.runtime_status = 'running' if check.get('ok') else 'error'
        site.deployment_status = 'success' if check.get('ok') else 'failed'
        site.last_restart_at = datetime.now()
    db.session.commit()
    log_action(f'site.runtime.{action}', site.name)
    flash(f'Runtime: {action} completed.', 'success')
    return redirect(url_for('manage_site', folder_name=site.folder_name))


@app.route('/site/<int:site_id>/environment', methods=['POST'])
def site_environment(site_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id']); site = db.session.get(Site, site_id)
    require_application_permission(user, site, 'environment.manage')
    key = (request.form.get('key') or '').strip().upper()
    value = request.form.get('value') or ''
    action = request.form.get('action', 'save')
    if not re.fullmatch(r'[A-Z_][A-Z0-9_]{0,119}', key):
        flash('Invalid environment variable name.', 'error')
    else:
        row = EnvironmentVariable.query.filter_by(application_id=site.id, key=key).first()
        if action == 'delete':
            if row:
                secret_path = row.secret_ref; db.session.delete(row); db.session.commit()
                if secret_path and os.path.isfile(secret_path): os.remove(secret_path)
                sync_runtime_environment(site)
            flash('Environment variable deleted.', 'success')
        elif not value:
            flash('Environment value cannot be empty.', 'error')
        else:
            set_environment_value(site, user, key, value); db.session.commit(); sync_runtime_environment(site)
            log_action('environment.update', f'{site.name}:{key}')
            flash('Environment variable saved. Value is write-only.', 'success')
    return redirect(url_for('manage_site', folder_name=site.folder_name))


@app.route('/site/<int:site_id>/database', methods=['POST'])
def site_database_create(site_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id']); site = db.session.get(Site, site_id)
    require_application_permission(user, site, 'database.create')
    engine = (request.form.get('engine') or 'mysql').strip().lower()
    if engine != 'mysql':
        flash('PostgreSQL is not provisionable on this host yet.' if get_current_language() == 'en'
              else 'PostgreSQL поки недоступний для створення на цьому сервері.', 'error')
        return redirect(request.referrer or url_for('user_databases_index'))
    display_name = (request.form.get('database_name') or site.name or 'database').strip()[:80]
    database_name, database_user = generated_database_identifiers(site.user_id, display_name)
    password = secrets.token_urlsafe(32)
    try:
        provision_mysql_database(database_name, database_user, password)
        resource = DatabaseResource(application_id=site.id, display_name=display_name, engine='mysql', database_name=database_name,
                                    database_user=database_user, host=MYSQL_HOST, port=MYSQL_PORT, secret_ref='pending', status='ready', created_by=user.id)
        db.session.add(resource); db.session.flush()
        resource.secret_ref = write_application_secret(site.id, 'database', resource.id, password)
        for key, value in {
            'DB_HOST': MYSQL_HOST, 'DB_PORT': str(MYSQL_PORT), 'DB_NAME': database_name,
            'DB_USER': database_user, 'DB_PASSWORD': password,
            'DATABASE_URL': f'mysql://{database_user}:{urllib.parse.quote(password, safe="")}@{MYSQL_HOST}:{MYSQL_PORT}/{database_name}',
        }.items(): set_environment_value(site, user, key, value)
        if site.application_type == 'wordpress':
            for key, value in {
                'WORDPRESS_DB_HOST': f'{MYSQL_HOST}:{MYSQL_PORT}', 'WORDPRESS_DB_NAME': database_name,
                'WORDPRESS_DB_USER': database_user, 'WORDPRESS_DB_PASSWORD': password,
                'WORDPRESS_CONFIG_EXTRA': "define('DISALLOW_FILE_EDIT', true); define('DISABLE_WP_CRON', true); if (!empty($_SERVER['HTTP_X_FORWARDED_PROTO']) && $_SERVER['HTTP_X_FORWARDED_PROTO'] === 'https') { $_SERVER['HTTPS']='on'; }",
            }.items(): set_environment_value(site, user, key, value)
        db.session.commit(); sync_runtime_environment(site)
        log_action('database.create', f'{site.name}:{database_name}')
        flash(f'Database {database_name} created. Credentials were added as write-only environment variables.', 'success')
    except (RuntimeError, ValueError, pymysql.MySQLError) as exc:
        db.session.rollback()
        try: deprovision_mysql_database(database_name, database_user)
        except Exception: pass
        flash(f'Database provisioning failed: {mask_sensitive_text(str(exc))[:300]}', 'error')
    return redirect(request.referrer or url_for('user_databases_index'))


@app.route('/site/<int:site_id>/wordpress/database-credentials', methods=['POST'])
def wordpress_database_credentials(site_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id']); site = db.session.get(Site, site_id)
    require_application_permission(user, site, 'database.view')
    if site.application_type != 'wordpress':
        abort(404)
    resource = DatabaseResource.query.filter_by(application_id=site.id, engine='mysql').first_or_404()
    password = read_application_secret(resource.secret_ref, site.id)
    response = render_template('wordpress_database_credentials.html', site=site, resource=resource, password=password)
    return response, 200, {'Cache-Control': 'no-store, max-age=0', 'Pragma': 'no-cache', 'X-Robots-Tag': 'noindex'}


@app.route('/site/<int:site_id>/wordpress/prepare-config', methods=['POST'])
def wordpress_prepare_config(site_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id']); site = db.session.get(Site, site_id)
    require_application_permission(user, site, 'files.upload')
    if site.application_type != 'wordpress':
        abort(404)
    access = ensure_application_access(site); root = application_root(access, bucket='file')
    resource_id = request.form.get('resource_id', type=int)
    resource = DatabaseResource.query.filter_by(id=resource_id, application_id=site.id, engine='mysql').first_or_404()
    target = os.path.join(root, 'wp-config.php')
    if os.path.exists(target):
        if request.form.get('replace_existing') != '1':
            flash('Конфігурація WordPress уже існує. Заміна потребує явного підтвердження.', 'error')
            return redirect(url_for('manage_site', folder_name=site.folder_name) + '#wordpress-setup')
        backup_target = target + '.before-myh-' + datetime.now().strftime('%Y%m%d-%H%M%S')
        shutil.copy2(target, backup_target)
    try:
        content = wordpress_config_contents(resource, request.form.get('table_prefix', 'wp_').strip())
        temporary = target + '.tmp'
        with open(temporary, 'w', encoding='utf-8') as handle:
            handle.write(content)
        os.chmod(temporary, 0o640); os.replace(temporary, target)
        log_action('wordpress.config.prepared', f'site={site.id}; database={resource.id}')
        flash('wp-config.php підготовлено. Завершіть встановлення у стандартному майстрі WordPress.', 'success')
    except (OSError, RuntimeError, ValueError) as exc:
        flash(mask_sensitive_text(str(exc)), 'error')
    return redirect(url_for('manage_site', folder_name=site.folder_name) + '#wordpress-setup')


@app.route('/site/<int:site_id>/runtime/logs')
def site_runtime_logs(site_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    site = db.session.get(Site, site_id)
    require_application_permission(user, site, 'logs.view')
    access = ensure_application_access(site)
    metadata_path = os.path.join(access.deployment_root, 'runtime.json')
    if not os.path.isfile(metadata_path):
        abort(404)
    with open(metadata_path, encoding='utf-8') as handle:
        metadata = json.load(handle)
    process = subprocess.run(
        ['docker', 'compose', '-p', metadata['project'], '-f', os.path.join(access.deployment_root, 'compose.yml'), 'logs', '--tail', '300', '--no-color'],
        capture_output=True, text=True, timeout=20, check=False,
    )
    return (mask_sensitive_text(process.stdout + process.stderr), 200, {'Content-Type': 'text/plain; charset=utf-8'})


@app.route('/api/capabilities')
def api_capabilities():
    user = db.session.get(User, session.get('user_id')) if session.get('user_id') else None
    if not user or user.is_banned:
        return jsonify({'error': {'code': 'AUTH_REQUIRED', 'message': 'Authentication required.', 'requestId': g.request_id}}), 401
    return jsonify({'capabilities': user_capabilities(user), 'requestId': g.request_id})


@app.route('/api/status-model')
def api_status_model():
    user = db.session.get(User, session.get('user_id')) if session.get('user_id') else None
    if not user or user.is_banned:
        abort(401)
    return jsonify({'statuses': STATUS_MODEL, 'requestId': g.request_id})


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


@app.route('/api/health')
def api_health():
    response = jsonify({'status': 'ok'})
    response.headers['Cache-Control'] = 'no-store'
    return response, 200


@app.route('/api/public-status')
def api_public_status():
    return jsonify({
        'online': True,
        'version': APP_VERSION,
    })


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
    require_application_permission(user, site, 'health.view')
    url = f'https://{site.name}.myh.guru/'
    started = time.monotonic()
    try:
        request_object = urllib.request.Request(url, method='HEAD', headers={'User-Agent': 'myh-monitor/1.0'})
        with urllib.request.urlopen(request_object, timeout=5) as response:
            status_code = response.status
        return jsonify({'online': status_code < 500, 'status': status_code, 'latency_ms': round((time.monotonic() - started) * 1000)})
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        return jsonify({'online': False, 'status': None, 'latency_ms': round((time.monotonic() - started) * 1000), 'error': str(exc)[:120]})


@app.route('/api/sites/<int:site_id>/php-health')
def api_site_php_health(site_id):
    if 'user_id' not in session:
        return jsonify({'ok': False, 'error': 'unauthorized'}), 401
    user = db.session.get(User, session['user_id'])
    site = db.session.get(Site, site_id)
    require_application_permission(user, site, 'health.view')

    if site.php_version != 'php':
        return jsonify({'ok': False, 'error': 'not a php site'}), 400

    access = ensure_application_access(site)
    site_root = application_root(access, bucket='file')
    stack_root = application_root(access, bucket='deployment')

    index_php = os.path.join(site_root, 'index.php')
    metadata_path = os.path.join(stack_root, 'panel-metadata.json')
    runtime_version = None
    runtime_language = None
    metadata_ok = False
    if os.path.isfile(metadata_path):
        try:
            with open(metadata_path, 'r', encoding='utf-8') as meta_handle:
                payload = json.load(meta_handle)
            runtime = payload.get('runtime') or {}
            runtime_language = (runtime.get('language') or '').strip().lower() or None
            runtime_version = (runtime.get('version') or '').strip() or None
            metadata_ok = True
        except (OSError, ValueError, json.JSONDecodeError):
            metadata_ok = False

    image = f'php:{runtime_version or site.runtime_version}-fpm-alpine'
    php_code, _ = run_command(['docker', 'image', 'inspect', image], timeout=10)
    php_available = php_code == 0
    php_line = f'container image {image}'

    bootstrap_exists = os.path.isfile(index_php)
    ok = bootstrap_exists and metadata_ok
    return jsonify({
        'ok': ok,
        'site': site.name,
        'bootstrap_exists': bootstrap_exists,
        'metadata_ok': metadata_ok,
        'runtime_language': runtime_language,
        'runtime_version': runtime_version,
        'php_binary_available': php_available,
        'php_binary_version_line': php_line,
    }), (200 if ok else 503)


@app.route('/api/php/runtime-check', methods=['POST'])
def api_php_runtime_check():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'unauthorized'}), 401
    user = db.session.get(User, session['user_id'])
    if not user_has_role_permission(user, 'site.create'):
        return jsonify({'success': False, 'error': 'forbidden'}), 403

    payload = request.get_json(silent=True) or {}
    selected_version = (payload.get('php_runtime') or '').strip() or '8.2'
    if selected_version not in set(RUNTIME_VERSIONS['php']):
        return jsonify({'success': False, 'error': 'unsupported version'}), 400

    image = f'php:{selected_version}-fpm-alpine'
    host_code, host_output = run_command(['php', '--version'], timeout=10)
    host_version = parse_php_version_string(host_output) if host_code == 0 else None
    code, _ = run_command(['docker', 'image', 'inspect', image], timeout=10)
    available_code, _ = run_command(['docker', 'manifest', 'inspect', image], timeout=20)
    available = bool(host_version) or code == 0 or available_code == 0
    installed_version = host_version or (selected_version if code == 0 else None)
    compatible = php_runtime_compatible(selected_version, host_version) if host_version else available
    return jsonify({
        'success': True,
        'selected_version': selected_version,
        'php_available': available,
        'installed_version': installed_version,
        'compatible': compatible,
        'version_line': image,
    })


@app.route('/developer/logs')
@developer_required
def developer_logs():
    user = db.session.get(User, session['user_id'])
    source = request.args.get('source', 'panel')
    lines = request.args.get('lines', default=200, type=int) or 200
    lines = max(50, min(lines, 500))

    if not user or (user_role(user) not in {'admin'} and not user.is_admin):
        log_lines = collect_application_logs_for_user(user, lines=lines)
        scoped_sources = {
            'app': {'label': 'assigned application logs'},
        }
        return render_template('developer_logs.html', source='app', source_label='assigned application logs', lines=lines, log_lines=log_lines, log_sources=scoped_sources)

    source_key = source if source in LOG_SOURCES else 'panel'
    log_config = LOG_SOURCES[source_key]
    code, output = run_command(log_config['command'][:], timeout=20)
    if code != 0:
        output = output or 'Log source unavailable.'
    log_lines = output.splitlines()
    if len(log_lines) > lines:
        log_lines = log_lines[-lines:]
    log_lines = [mask_sensitive_text(item) for item in log_lines]
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
@developer_required
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


@app.route('/developer/system-audit')
@developer_required
def developer_system_audit():
    audit = build_system_audit()
    return render_template('developer_system_audit.html', audit=audit)


@app.route('/developer/applications')
@developer_required
def developer_applications():
    user = db.session.get(User, session['user_id'])
    registry = build_application_registry()
    if user_role(user) != 'admin' and not user.is_admin:
        allowed_ids = set(assigned_application_ids(user))
        registry['applications'] = [item for item in registry['applications'] if item['id'] in allowed_ids]
        registry['summary'] = {
            'total': len(registry['applications']),
            'online': sum(1 for item in registry['applications'] if item['status'] == 'online'),
            'offline': sum(1 for item in registry['applications'] if item['status'] == 'offline'),
            'degraded': sum(1 for item in registry['applications'] if item['status'] == 'degraded'),
            'backups': sum(item['backup_count'] for item in registry['applications']),
        }
    return render_template('developer_applications.html', registry=registry)


@app.route('/developer/docker')
@developer_required
def developer_docker():
    user = db.session.get(User, session['user_id'])
    can_manage_docker = bool(user and (user_role(user) == 'admin' or user.is_admin))
    containers = list_scoped_docker_containers(user)
    return render_template('developer_docker.html', containers=containers, can_manage_docker=can_manage_docker)


@app.route('/developer/notifications')
@admin_required
def developer_notifications():
    return render_template('developer_notifications.html', provider_statuses=notification_statuses())


def notification_response(message, success=True, status_code=200):
    if request.is_json:
        return jsonify({'success': success, 'message': message}), status_code
    flash(message, 'success' if success else 'error')
    return redirect(url_for('developer_notifications'))


def valid_notification_email(value):
    return bool(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value or '')) and len(value) <= 254


@app.route('/api/admin/notifications/settings')
@admin_required
def api_notification_settings():
    return jsonify({'providers': notification_statuses()})


@app.route('/api/admin/notifications/<provider>/configure', methods=['POST'])
@admin_required
def api_notification_configure(provider):
    if provider not in {'telegram', 'smtp'}:
        abort(404)
    payload = request.get_json(silent=True) if request.is_json else request.form
    payload = payload or {}
    existing = notification_settings().get(provider) or {}
    try:
        if provider == 'telegram':
            token = str(payload.get('bot_token') or '').strip()
            chat_id = str(payload.get('chat_id') or '').strip()
            effective_token = token or existing.get('bot_token') or os.environ.get('TELEGRAM_BOT_TOKEN', '')
            if not re.fullmatch(r'\d{5,15}:[A-Za-z0-9_-]{20,}', effective_token):
                return notification_response('Bot Token має некоректний формат.', False, 400)
            if not re.fullmatch(r'(?:-?\d{5,20}|@[A-Za-z0-9_]{5,32})', chat_id):
                return notification_response('Chat ID має некоректний формат.', False, 400)
            values = {'bot_token': token, 'chat_id': chat_id, 'enabled': str(payload.get('enabled', '')).lower() in {'1', 'true', 'on', 'yes'}}
        else:
            host = str(payload.get('host') or '').strip().lower()
            username = str(payload.get('username') or '').strip()
            password = str(payload.get('password') or '')
            sender = str(payload.get('sender') or '').strip()
            recipients = [item.strip() for item in str(payload.get('recipients') or '').split(',') if item.strip()]
            encryption = str(payload.get('encryption') or 'tls').lower()
            try:
                port = int(payload.get('port') or 0)
            except (TypeError, ValueError):
                port = 0
            effective_password = password or existing.get('password') or os.environ.get('SMTP_PASSWORD', '')
            if not re.fullmatch(r'(?=.{1,253}$)[A-Za-z0-9.-]+', host) or '..' in host:
                return notification_response('Вкажіть коректний SMTP host.', False, 400)
            if port < 1 or port > 65535:
                return notification_response('SMTP port має бути від 1 до 65535.', False, 400)
            if not valid_notification_email(sender) or not recipients or len(recipients) > 10 or not all(valid_notification_email(item) for item in recipients):
                return notification_response('Перевірте From Email та адреси отримувачів.', False, 400)
            if encryption not in {'tls', 'ssl', 'none'}:
                return notification_response('Непідтримуваний тип шифрування SMTP.', False, 400)
            if username and not effective_password:
                return notification_response('Для SMTP username потрібен пароль.', False, 400)
            values = {'host': host, 'port': port, 'username': username, 'password': password,
                      'sender': sender, 'recipients': recipients, 'encryption': encryption,
                      'enabled': str(payload.get('enabled', '')).lower() in {'1', 'true', 'on', 'yes'}}
        notification_config_store().update_provider(provider, values)
        log_action(f'notification.{provider}.configure', 'credentials updated; secrets omitted')
        return notification_response('Налаштування збережено. Виконайте реальний тест підключення.')
    except NotificationConfigError:
        return notification_response('Не вдалося безпечно зберегти налаштування.', False, 500)


@app.route('/api/admin/notifications/<provider>/toggle', methods=['POST'])
@admin_required
def api_notification_toggle(provider):
    if provider not in {'telegram', 'smtp'}:
        abort(404)
    payload = request.get_json(silent=True) if request.is_json else request.form
    enabled = str((payload or {}).get('enabled', '')).lower() in {'1', 'true', 'on', 'yes'}
    rows = {item['provider']: item for item in notification_statuses()}
    if enabled and not rows[provider]['credentials_saved']:
        return notification_response('Спочатку збережіть повні налаштування provider.', False, 400)
    notification_config_store().set_enabled(provider, enabled)
    log_action(f"notification.{provider}.{'enable' if enabled else 'disable'}", 'provider state changed')
    return notification_response('Provider увімкнено.' if enabled else 'Provider вимкнено.')


@app.route('/api/admin/notifications/<provider>/test', methods=['POST'])
@admin_required
def api_notification_test(provider):
    if provider not in {'telegram', 'smtp'}:
        abort(404)
    if not notification_test_rate_allowed(provider):
        return notification_response('Забагато тестів. Повторіть пізніше.', False, 429)
    rows = {item['provider']: item for item in notification_statuses()}
    if not rows[provider]['credentials_saved']:
        return notification_response(f"Спочатку налаштуйте {'Telegram' if provider == 'telegram' else 'SMTP'}.", False, 400)
    settings = notification_settings()
    temporary_settings = json.loads(json.dumps(settings))
    temporary_settings.setdefault(provider, {})['enabled'] = True
    service = NotificationService.from_env(
        os.path.join(app.instance_path, 'notification_state.json'), stored_settings=temporary_settings
    )
    message = 'MyH: тестове сповіщення успішно доставлено.'
    result = service.send(message, level='test', force=True, provider_name=provider)
    succeeded = bool(result.get('delivered'))
    notification_config_store().record_test(provider, succeeded, datetime.now(timezone.utc).isoformat())
    log_action(f'notification.{provider}.test', f"result={'success' if succeeded else 'failed'}")
    if succeeded:
        return notification_response('Тестове сповіщення фактично доставлено provider-ом.')
    return notification_response('Provider не підтвердив доставку. Перевірте credentials і мережеві параметри.', False, 502)


@app.route('/api/webhook/notify', methods=['POST'])
def api_webhook_notify():
    if not external_webhook_rate_allowed('notify'):
        return jsonify({'success': False, 'error': 'rate limit exceeded'}), 429
    if not NOTIFY_WEBHOOK_SECRET:
        return jsonify({'success': False, 'error': 'webhook disabled'}), 503
    if not is_signed_webhook_secret_valid(request, NOTIFY_WEBHOOK_SECRET):
        return jsonify({'success': False, 'error': 'forbidden'}), 403
    if (request.content_length or 0) > 16 * 1024:
        return jsonify({'success': False, 'error': 'payload too large'}), 413

    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({'success': False, 'error': 'invalid payload'}), 400
    message = payload.get('message') or payload.get('event')
    if not isinstance(message, str) or not message.strip() or len(message) > 500:
        return jsonify({'success': False, 'error': 'invalid message'}), 400
    safe_message = message.strip()
    send_notification(safe_message, level='info')
    log_action('webhook.notify', 'accepted external notification')
    return jsonify({'success': True, 'message': safe_message})


@app.route('/api/github/webhook', methods=['POST'])
def api_github_webhook():
    if not external_webhook_rate_allowed('github'):
        return jsonify({'success': False, 'error': 'rate limit exceeded'}), 429
    if (request.content_length or 0) > 1024 * 1024:
        return jsonify({'success': False, 'error': 'payload too large'}), 413
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({'success': False, 'error': 'invalid payload'}), 400
    repo = payload.get('repository', {}).get('full_name') or payload.get('repository', {}).get('name') or 'unknown'
    ref = payload.get('ref') or 'unknown'
    site_name = payload.get('site_name') or 'default'
    site = Site.query.filter_by(name=site_name).first() if site_name != 'default' else None
    expected_secret = site.webhook_secret if site and site.webhook_secret else os.environ.get('GITHUB_WEBHOOK_SECRET', '').strip()
    if not expected_secret:
        return jsonify({'success': False, 'error': 'webhook secret not configured'}), 503
    if not github_signature_valid(request, expected_secret):
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
            access = ensure_application_access(site)
            deploy_from_git(payload.get('repository', {}).get('clone_url') or payload.get('repository', {}).get('html_url') or '', application_root(access, bucket='file'), access=access)
            record_deployment_event(site.name, deploy_mode='git', status='success', detail='GitHub webhook deployment completed', repo_url=payload.get('repository', {}).get('clone_url'))
            append_deploy_log(site.name, 'GitHub webhook deployment completed')
            log_action('deploy.github', f'{site.name}:{repo}')
        except Exception as exc:
            safe_error = mask_sensitive_text(str(exc))[:500]
            record_deployment_event(site.name, deploy_mode='git', status='failed', detail=safe_error, repo_url=payload.get('repository', {}).get('clone_url'))
            append_deploy_log(site.name, f'GitHub webhook deployment failed: {safe_error}')
            log_action('deploy.github.failed', f'{site.name}:{repo}')
            return jsonify({'success': False, 'error': 'deployment failed', 'requestId': g.request_id}), 500
    return jsonify({'success': True, 'message': message})


@app.route('/developer/deploy/history')
@app.route('/dashboard/deploy/history')
def developer_deploy_history():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    if not user:
        abort(403)
    role = user_role(user)
    events = DeploymentEvent.query.order_by(DeploymentEvent.created_at.desc()).limit(200).all()
    if role != 'admin' and not user.is_admin:
        allowed_names = {site.name for site in Site.query.filter(Site.id.in_(assigned_application_ids(user))).all()}
        events = [event for event in events if event.site_name in allowed_names]
    else:
        events = events[:50]
    back_to_panel_endpoint = 'developer_dashboard' if role in {'developer', 'admin'} or user.is_admin else 'dashboard'
    deploy_endpoint = 'developer_deploy'
    return render_template('developer_deploy_history.html', events=events, back_to_panel_endpoint=back_to_panel_endpoint, deploy_endpoint=deploy_endpoint)


@app.route('/developer/deploy', methods=['GET', 'POST'])
@app.route('/dashboard/deploy', methods=['GET', 'POST'])
def developer_deploy():
    def normalize_deploy_target(raw_value):
        candidate = (raw_value or '').strip()
        if not candidate:
            return ''
        parsed = urllib.parse.urlparse(candidate)
        if parsed.scheme and parsed.netloc:
            candidate = parsed.netloc
        candidate = candidate.split('/', 1)[0].split(':', 1)[0].strip()
        return candidate

    def resolve_deploy_site(target_value, allowed_ids=None):
        candidate = normalize_deploy_target(target_value)
        if not candidate:
            return None
        candidate_lower = candidate.lower()
        query = Site.query.filter(
            (func.lower(Site.name) == candidate_lower)
            | (func.lower(Site.folder_name) == candidate_lower)
            | (func.lower(func.coalesce(Site.custom_domain, '')) == candidate_lower)
        )
        site = query.first()
        if not site and candidate.isdigit():
            site = db.session.get(Site, int(candidate))
        if site and allowed_ids is not None and site.id not in allowed_ids:
            return None
        return site

    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    if not user:
        abort(403)
    role = user_role(user)
    allowed_site_ids = set(assigned_application_ids(user)) if role != 'admin' and not user.is_admin else None
    back_to_panel_endpoint = 'developer_dashboard' if role in {'developer', 'admin'} or user.is_admin else 'dashboard'
    available_sites_query = Site.query.order_by(Site.name.asc())
    if allowed_site_ids is not None:
        available_sites_query = available_sites_query.filter(Site.id.in_(allowed_site_ids))
    available_sites = available_sites_query.all()

    if request.method == 'POST':
        if request.form.get('action') == 'save_webhook_config':
            site_name = (request.form.get('site_name') or '').strip()
            secret = (request.form.get('webhook_secret') or '').strip()
            branch = (request.form.get('webhook_branch') or '').strip()
            if not site_name:
                flash('Site name is required.', 'error')
                return redirect(url_for('developer_deploy'))
            site = resolve_deploy_site(site_name, allowed_site_ids)
            if not site:
                flash('Target site was not found.', 'error')
                return redirect(url_for('developer_deploy'))
            require_application_permission(user, site, 'integration.manage')
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
        site = resolve_deploy_site(target_name, allowed_site_ids)
        if not site:
            hint_names = ', '.join(item.name for item in available_sites[:8])
            hint_tail = '...' if len(available_sites) > 8 else ''
            if hint_names:
                flash(f'Target site was not found. Available sites: {hint_names}{hint_tail}', 'error')
            else:
                flash('Target site was not found. No available sites for your account.', 'error')
            return redirect(url_for('developer_deploy'))
        require_application_permission(user, site, 'deployment.execute')
        if deploy_mode == 'git':
            require_application_permission(user, site, 'git.connect')
        access = ensure_application_access(site)
        site_path = application_root(access, bucket='file')
        os.makedirs(site_path, exist_ok=True)
        if deploy_mode == 'git':
            repo_url = (request.form.get('repo_url') or '').strip()
            github_token = (request.form.get('github_token') or '').strip()
            branch = (request.form.get('branch') or 'main').strip()
            if not repo_url:
                flash('Git repository URL is required.', 'error')
                return redirect(url_for('developer_deploy'))
            if not validate_git_repository_url(repo_url) or not GIT_BRANCH_PATTERN.fullmatch(branch):
                flash('Git URL or branch is invalid.', 'error')
                return redirect(url_for('developer_deploy'))
            deploy_tmp_root = os.path.join(app.instance_path, 'deploy_tmp'); os.makedirs(deploy_tmp_root, exist_ok=True)
            staged_repo = tempfile.mkdtemp(prefix=f'deploy-{site.id}-', dir=deploy_tmp_root)
            try:
                clone_url = build_authenticated_repo_url(repo_url, git_token=github_token)
                subprocess.run(['git', 'clone', '--depth', '1', '--branch', branch, '--single-branch', clone_url, staged_repo],
                               check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=180)
                git_metadata = os.path.join(staged_repo, '.git')
                if os.path.isdir(git_metadata): shutil.rmtree(git_metadata)
                detected = activate_staged_deployment(site, access, staged_repo)
            except subprocess.CalledProcessError as exc:
                safe_output = sanitize_git_error_output(exc.output, git_token=github_token)
                if 'could not read Username' in safe_output or 'Authentication failed' in safe_output or 'Repository not found' in safe_output:
                    flash('Git deploy failed: repository requires access. Add GitHub token for private repository.', 'error')
                else:
                    flash(f'Git deploy failed: {safe_output[:500]}', 'error')
                return redirect(url_for('developer_deploy'))
            except Exception as exc:
                record_deployment_event(site.name, deploy_mode='git', status='failed', detail=str(exc)[:500], repo_url=repo_url)
                flash(f'Git deploy failed: {str(exc)[:500]}', 'error')
                return redirect(url_for('developer_deploy'))
            finally:
                shutil.rmtree(staged_repo, ignore_errors=True)
            record_deployment_event(site.name, deploy_mode='git', status='success', detail='Git deployment completed', repo_url=repo_url)
            record_upload_history(site, user, repo_url, 0, source='git', status='success', deployment='deploy-git', detail='git deployment')
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
            staged_zip = tempfile.mkdtemp(prefix=f'deploy-{site.id}-', dir=os.path.join(app.instance_path, 'deploy_tmp'))
            try:
                with zipfile.ZipFile(temp_path, 'r') as archive:
                    safe_extract_zip(archive, staged_zip)
                staged_source = staged_zip
                nested_root = detect_single_root_folder(staged_zip)
                if nested_root:
                    staged_source = os.path.join(staged_zip, nested_root)
                activate_staged_deployment(site, access, staged_source)
            except (zipfile.BadZipFile, ValueError, RuntimeError) as exc:
                record_deployment_event(site.name, deploy_mode='zip', status='failed', detail=str(exc)[:500], repo_url=None)
                flash(f'Invalid archive: {exc}', 'error')
                return redirect(url_for('developer_deploy'))
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                shutil.rmtree(staged_zip, ignore_errors=True)
            record_deployment_event(site.name, deploy_mode='zip', status='success', detail=f'ZIP deployment completed: {uploaded.filename}', repo_url=None)
            record_upload_history(site, user, uploaded.filename, 0, source='panel', status='success', deployment='deploy-zip', detail='zip deployment')
            log_action('deploy.zip', f'{site.name}:{uploaded.filename}')
            flash('ZIP deployment completed.', 'success')
        return redirect(url_for('developer_deploy'))
    return render_template('developer_deploy.html', available_sites=available_sites, back_to_panel_endpoint=back_to_panel_endpoint)


@app.route('/developer/runtimes', methods=['GET', 'POST'])
@admin_required
def developer_runtimes():
    rows, config = runtime_inventory()
    application_counts = dict(db.session.query(Site.runtime_type, func.count(Site.id)).group_by(Site.runtime_type).all())
    for row in rows:
        row['applications'] = int(application_counts.get(row['runtime'], 0))
    if request.method == 'POST':
        runtime = (request.form.get('runtime') or '').strip(); version = (request.form.get('version') or '').strip()
        action = (request.form.get('action') or '').strip()
        if action == 'verify_all':
            result = subprocess.run(
                [os.path.join(app.root_path, 'venv', 'bin', 'python'), os.path.join(app.root_path, 'scripts', 'runtime_smoke.py'), '--output', RUNTIME_HEALTH_FILE],
                cwd=app.root_path, capture_output=True, text=True, timeout=600, check=False,
                env={**os.environ, 'PYTHONPATH': app.root_path},
            )
            if os.path.isfile(RUNTIME_HEALTH_FILE):
                os.chmod(RUNTIME_HEALTH_FILE, 0o600)
            log_action('runtime.verify_all', f'exit={result.returncode}')
            flash('Runtime verification passed.' if result.returncode == 0 else 'Runtime verification found unavailable environments.', 'success' if result.returncode == 0 else 'error')
            return redirect(url_for('developer_runtimes'))
        row = next((item for item in rows if item['runtime'] == runtime and item['version'] == version and item.get('image')), None)
        if not row:
            flash('Unsupported runtime target.', 'error')
        elif action == 'install':
            code, output = run_command(['docker', 'pull', row['image']], timeout=300)
            flash('Runtime image installed.' if code == 0 else f'Runtime install failed: {output[-300:]}', 'success' if code == 0 else 'error')
        elif action in {'enable', 'disable'}:
            config[f'{runtime}:{version}'] = {'enabled': action == 'enable'}
            with open(RUNTIME_CONFIG_FILE + '.tmp', 'w', encoding='utf-8') as handle: json.dump(config, handle, indent=2)
            os.chmod(RUNTIME_CONFIG_FILE + '.tmp', 0o600); os.replace(RUNTIME_CONFIG_FILE + '.tmp', RUNTIME_CONFIG_FILE)
            flash('Runtime status updated.', 'success')
        elif action == 'default':
            config[runtime] = {'default': version}
            with open(RUNTIME_CONFIG_FILE + '.tmp', 'w', encoding='utf-8') as handle: json.dump(config, handle, indent=2)
            os.chmod(RUNTIME_CONFIG_FILE + '.tmp', 0o600); os.replace(RUNTIME_CONFIG_FILE + '.tmp', RUNTIME_CONFIG_FILE)
            flash('Default runtime updated. Existing applications remain pinned.', 'success')
        log_action('runtime.admin', f'{runtime}:{version}:{action}')
        return redirect(url_for('developer_runtimes'))
    return render_template('developer_runtimes.html', runtimes=rows)


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
@developer_required
def developer_backup_center():
    user = db.session.get(User, session['user_id'])
    role = user_role(user)
    if role == 'admin' or user.is_admin:
        sites = Site.query.order_by(Site.name).all()
    else:
        site_ids = assigned_application_ids(user)
        sites = Site.query.filter(Site.id.in_(site_ids)).order_by(Site.name).all() if site_ids else []
    backup_rows = []
    total_backups = 0
    total_size = 0
    sites_with_backups = 0
    for site in sites:
        access = ensure_application_access(site)
        quotas = quota_snapshot(access)
        backups = list_site_backups(site, access=access)
        if backups:
            sites_with_backups += 1
        total_backups += len(backups)
        total_size += sum(item['size'] for item in backups)
        backup_rows.append({
            'site': site,
            'access': access,
            'quotas': quotas,
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
    try:
        with open('/var/lib/myh-backup/status.json', encoding='utf-8') as handle:
            platform_backup = json.load(handle)
    except (OSError, ValueError):
        platform_backup = {'local_status': 'unknown', 'remote_status': 'not_configured', 'remote_type': 'none', 'timestamp': None, 'last_restore_test': None}
    return render_template(
        'developer_backup_center.html',
        summary=summary,
        backup_rows=backup_rows,
        recent_jobs=recent_jobs,
        platform_backup=platform_backup,
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
        edge_certificate=edge_tls_certificate_status(zone_name),
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
        personal_account = SftpAccount.query.filter_by(assigned_user_id=user.id, username=user.username, enabled=True).first()
        if personal_account:
            personal_account.password_hash = build_sftp_password_hash(new_password)
            personal_account.auth_type = 'password'
            db.session.commit()
            queue_sftp_provision(personal_account, 'reset-credentials', actor=user.username)
            record_sftp_audit(personal_account, 'self_password_synced', status='success')
        log_action('account.password', 'Пароль змінено')
        flash('Пароль успішно змінено.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/delete-file/<folder_name>', methods=['POST'])
def delete_file(folder_name):
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    user = db.session.get(User, session['user_id'])
    site = Site.query.filter_by(folder_name=folder_name).first()
    if not user or not site:
        return abort(404)
    access = require_application_permission(user, site, 'files.delete')
    site_root = application_root(access, bucket='file')
        
    file_to_delete = request.form.get('file_path')
    current_dir = normalized_relative_path(request.form.get('current_dir', ''))
    if file_to_delete:
        enforce_wordpress_path_permission(user, access, site_root, file_to_delete)
        file_path = safe_resource_path(access, file_to_delete, bucket='file')
        if os.path.isfile(file_path):
            size = os.path.getsize(file_path)
            os.remove(file_path)
            log_action('file.delete', f'{site.name}/{file_to_delete}')
            record_upload_history(site, user, file_to_delete, size, source='panel', status='success', deployment='', detail='delete-file')
        elif os.path.isdir(file_path):
            try:
                os.rmdir(file_path)
                log_action('directory.delete', f'{site.name}/{file_to_delete}')
                record_upload_history(site, user, file_to_delete, 0, source='panel', status='success', deployment='', detail='delete-directory')
            except OSError:
                flash('Каталог не порожній.', 'error')

    if current_dir:
        return redirect(url_for('manage_site', folder_name=folder_name, dir=current_dir))
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
