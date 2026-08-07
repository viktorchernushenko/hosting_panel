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
import shutil
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
    'application_access',
    'sftp_account',
    'upload_history',
    'sftp_audit_event',
}
USER_PERMISSION_SET = {
    'files.view',
    'files.upload',
    'files.download',
    'files.create',
    'files.rename',
    'files.delete',
    'sftp.access',
    'backup.view',
    'backup.create',
    'site.view',
    'deployment.request',
    'wp.uploads.manage',
}
DEVELOPER_PERMISSION_SET = {
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
SFTP_PROVISION_LOCK = threading.Lock()
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
        ]

        with db.engine.begin() as conn:
            for table_name, column_name, statement, existing in migrations:
                if table_name in table_names and column_name not in existing:
                    conn.execute(text(statement))
            if 'user' in table_names and 'quota_mb' in user_columns:
                conn.execute(text("UPDATE user SET quota_mb = 51200 WHERE quota_mb = 256"))
            conn.execute(text("UPDATE user SET role = 'admin' WHERE is_admin = 1 AND (role IS NULL OR role = '' OR role = 'user')"))
            conn.execute(text("UPDATE user SET role = 'user' WHERE role IS NULL OR role = ''"))

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

        inspector = inspect(db.engine)
        if 'application_access' in inspector.get_table_names():
            access_columns = {column['name'] for column in inspector.get_columns('application_access')}
            with db.engine.begin() as conn:
                if 'wordpress_permissions_user_json' not in access_columns:
                    conn.execute(text("ALTER TABLE application_access ADD COLUMN wordpress_permissions_user_json TEXT NOT NULL DEFAULT '[]'"))
                if 'wordpress_permissions_developer_json' not in access_columns:
                    conn.execute(text("ALTER TABLE application_access ADD COLUMN wordpress_permissions_developer_json TEXT NOT NULL DEFAULT '[]'"))

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


def ensure_application_access_registry():
    seeded = False
    for site in Site.query.order_by(Site.id.asc()).all():
        access = ApplicationAccess.query.filter_by(site_id=site.id).first()
        if access:
            continue
        default_root = os.path.join(app.config['UPLOAD_FOLDER'], site.folder_name)
        default_backup_root = os.path.join(app.instance_path, 'site_backups', str(site.id))
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
    if role in {'user', 'developer', 'admin'}:
        return role
    return 'admin' if user.is_admin else 'user'


def default_permissions_for_role(role):
    if role == 'admin':
        return sorted(ADMIN_PERMISSION_SET)
    if role == 'developer':
        return sorted(DEVELOPER_PERMISSION_SET)
    return sorted(USER_PERMISSION_SET)


def normalize_permission_list(values, role):
    requested = set(values or [])
    if role == 'developer':
        allowed = DEVELOPER_PERMISSION_SET
    elif role == 'admin':
        allowed = ADMIN_PERMISSION_SET
    else:
        allowed = USER_PERMISSION_SET
    cleaned = sorted(item for item in requested if item in allowed)
    return cleaned or default_permissions_for_role(role)


def ensure_application_access(site):
    access = ApplicationAccess.query.filter_by(site_id=site.id).first()
    if access:
        changed = False
        default_file_root = os.path.join(app.config['UPLOAD_FOLDER'], site.folder_name)
        default_backup_root = os.path.join(app.instance_path, 'site_backups', str(site.id))
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
    default_backup_root = os.path.join(app.instance_path, 'site_backups', str(site.id))
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


def queue_sftp_provision(account, action, actor='system'):
    payload = {
        'timestamp': datetime.now().isoformat(),
        'action': action,
        'account_id': account.id,
        'username': account.username,
        'role': account.role,
        'assigned_user_id': account.assigned_user_id,
        'assigned_applications': parse_json_list(account.assigned_applications_json, int),
        'chroot_directory': account.chroot_directory,
        'enabled': bool(account.enabled),
        'auth_type': account.auth_type,
        'password_hash': account.password_hash,
        'public_keys': parse_json_list(account.public_keys_json, str),
        'actor': actor,
    }
    with SFTP_PROVISION_LOCK:
        append_jsonl(SFTP_PROVISION_QUEUE_FILE, payload)
    account.system_state = 'queued'
    db.session.commit()
    try:
        subprocess.run(['sudo', '-n', 'systemctl', 'start', SFTP_PROVISION_SERVICE], check=False, timeout=8)
    except Exception:
        pass


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
    return os.environ.get('HOSTING_PANEL_SFTP_HOST', '').strip() or request.host.split(':', 1)[0]


def sftp_filezilla_payload(account):
    app_ids = parse_json_list(account.assigned_applications_json, int)
    assigned_sites = Site.query.filter(Site.id.in_(app_ids)).order_by(Site.name.asc()).all() if app_ids else []
    return {
        'protocol': 'SFTP',
        'host': sftp_connection_host(),
        'port': 22,
        'username': account.username,
        'root': account.chroot_directory,
        'auth_type': account.auth_type,
        'assigned_sites': assigned_sites,
    }


_LOG_SECRET_PATTERN = re.compile(r'(password|token|authorization|cookie|jwt|api[-_ ]?key|secret|db[_-]?pass)', re.IGNORECASE)


def mask_sensitive_text(line):
    if not line:
        return line
    if _LOG_SECRET_PATTERN.search(line):
        if '=' in line:
            left, _, _ = line.partition('=')
            return left + '=********'
        return _LOG_SECRET_PATTERN.sub('********', line)
    return line


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


def backup_directory(site, access=None):
    access = access or ensure_application_access(site)
    path = application_root(access, bucket='backup') if access.backup_root else os.path.join(app.instance_path, 'site_backups', str(site.id))
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
    destination = os.path.join(application_root(access, bucket='backup'), f'{timestamp}.zip')
    source = application_root(access, bucket='file')
    estimated_size = directory_size_safe(source)
    enforce_backup_quota(access, additional_bytes=estimated_size)
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for root, _, filenames in os.walk(source):
            for filename in filenames:
                full_path = os.path.join(root, filename)
                archive.write(full_path, os.path.relpath(full_path, source))
    enforce_backup_quota(access, additional_bytes=0)
    return destination


def extract_zip_to_site(zip_path, site_path):
    with zipfile.ZipFile(zip_path, 'r') as archive:
        safe_extract_zip(archive, site_path)


def deploy_from_git(repo_url, site_path, access=None):
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
    }
    public_ip = probe_public_ip()
    container_names = {item['name'] for item in containers}
    docker_images = {item['image'] for item in containers}
    readiness = [
        {
            'name': 'General Docker Hosting',
            'status': 'READY' if containers else 'WARNING',
            'detail': 'Docker engine is available and containers are present.' if containers else 'No running containers were detected.',
        },
        {
            'name': 'Reverse Proxy',
            'status': 'WARNING' if 'nginx-proxy-manager' in container_names else 'NOT READY',
            'detail': 'Nginx Proxy Manager is installed, but the shared proxy network is not normalized yet.',
        },
        {
            'name': 'WordPress Ready',
            'status': 'NOT READY' if 'wordpress' not in docker_images else 'WARNING',
            'detail': 'WordPress stack is not standardized yet.',
        },
        {
            'name': 'Nextcloud',
            'status': 'NOT READY' if 'nextcloud' in restarting else 'WARNING',
            'detail': 'Nextcloud is currently in a restart loop due to a data/image version mismatch.',
        },
        {
            'name': 'Backups',
            'status': 'WARNING',
            'detail': 'Per-site backups exist, but centralized backup registry is still missing.',
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
    if 'nextcloud' in restarting:
        problems.append('Nextcloud data volume is ahead of the current image version.')
    if 'lab-db' in restarting:
        problems.append('MariaDB crash recovery is failing on tc.log.')
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
            'type': 'Static',
            'owner': site.owner.username if site.owner else '—',
            'domain': domain,
            'path': access.file_root,
            'upload_root': access.upload_root,
            'deployment_root': access.deployment_root,
            'backup_root': access.backup_root,
            'stack': 'site-files',
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
    access = ensure_application_access(site)
    backups = list_site_backups(site, access=access)
    for old in backups[limit:]:
        try:
            os.remove(os.path.join(backup_directory(site, access=access), old['name']))
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


def developer_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        user = User.query.get(session['user_id'])
        if not user:
            return abort(403)
        if user_role(user) not in {'developer', 'admin'} and not user.is_admin:
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
            ensure_application_access(new_site)
            log_action('site.create', f'{subdomain} → {owner.username}')
        else:
            flash(translate('invalid_site_name'), 'error')
            
        return redirect(url_for('dashboard'))
    
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


@app.route('/dashboard/sftp-access')
def user_sftp_access():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    if not user:
        abort(403)
    if user_role(user) in {'developer', 'admin'}:
        return redirect(url_for('developer_sftp_access'))
    site_ids = assigned_application_ids(user)
    sites = Site.query.filter(Site.id.in_(site_ids)).order_by(Site.name.asc()).all() if site_ids else []
    accounts = SftpAccount.query.filter_by(assigned_user_id=user.id, enabled=True).order_by(SftpAccount.username.asc()).all()
    payloads = [sftp_filezilla_payload(account) for account in accounts]
    return render_template('user_sftp_access.html', user=user, sites=sites, sftp_connections=payloads)


@app.route('/developer/sftp-access')
@developer_required
def developer_sftp_access():
    user = db.session.get(User, session['user_id'])
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
    return render_template('developer_sftp_users.html', users=users, sites=sites, accounts=accounts, audit_rows=audit_rows, account_assignments=account_assignments)


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
        password_hash=generate_password_hash(temp_password) if temp_password else None,
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
    account.password_hash = generate_password_hash(temporary_password)
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
        site = db.session.get(Site, site_ids[0])
        if site:
            access = ensure_application_access(site)
            account.chroot_directory = access.upload_root or access.file_root
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
    require_application_permission(user, site, 'deployment.request')
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
    
    user = User.query.get(session['user_id'])
    site = Site.query.filter_by(folder_name=folder_name).first()
    if not user or not site:
        return abort(404)
    access = ensure_application_access(site)
    if request.method == 'GET':
        require_application_permission(user, site, 'files.view')
    site_path = application_root(access, bucket='file')
    os.makedirs(site_path, exist_ok=True)
    
    if request.method == 'POST':
        action = request.form.get('action', 'upload')
        if action == 'mkdir':
            require_application_permission(user, site, 'files.create')
            folder = request.form.get('folder_name', '').strip().replace('\\', '/')
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
                return redirect(url_for('manage_site', folder_name=folder_name))
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
        else:
            require_application_permission(user, site, 'files.upload')
            target_dir_relative = request.form.get('target_dir', '')
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
                if filename.lower().endswith('.zip'):
                    try:
                        with zipfile.ZipFile(file_path, 'r') as zip_ref:
                            extracted_size = sum(item.file_size for item in zip_ref.infolist())
                            if len(zip_ref.infolist()) > 1000 or extracted_size > 128 * 1024 * 1024:
                                raise ValueError('Архів перевищує безпечний ліміт')
                            if user_usage_bytes(site.owner) - upload_size + extracted_size > quota_bytes:
                                raise ValueError('Розпакований архів перевищить квоту користувача')
                            if application_usage_bytes - upload_size + extracted_size > application_quota_bytes:
                                raise ValueError('Розпакований архів перевищить квоту застосунку')
                            for member in zip_ref.infolist():
                                member_rel = normalized_relative_path(os.path.join(target_dir_relative, member.filename))
                                enforce_wordpress_path_permission(user, access, site_path, member_rel)
                            safe_extract_zip(zip_ref, target_dir)
                        application_usage_bytes = application_usage_bytes_for_access(access)
                        record_upload_history(site, user, filename, upload_size, source='panel', status='success', deployment='', detail='zip-upload+extract')
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
        enforce_wordpress_path_permission(user, access, site_path, edit_path)
        target = safe_resource_path(access, edit_path, bucket='file')
        if os.path.isfile(target) and os.path.getsize(target) <= 1024 * 1024 and os.path.splitext(target)[1].lower() in TEXT_EXTENSIONS:
            try:
                with open(target, 'r', encoding='utf-8') as handle:
                    edit_content = handle.read()
            except UnicodeDecodeError:
                flash('Цей файл не є текстовим.', 'error')

    return render_template('manage_site.html', site=site, access=access, files=sorted(files, key=lambda item: item['path']), directories=sorted(directories), edit_path=edit_path, edit_content=edit_content, backups=list_site_backups(site, access=access), usage_bytes=user_usage_bytes(site.owner), quota=quota_snapshot(access))

@app.route('/view-site/<folder_name>/', defaults={'subpath': 'index.html'})
@app.route('/view-site/<folder_name>/<path:subpath>')
def view_site(folder_name, subpath):
    site = Site.query.filter_by(folder_name=folder_name).first()
    if not site or site.is_banned or (site.owner and site.owner.is_banned):
        return abort(404)

    access = ensure_application_access(site)
    site_path = application_root(access, bucket='file')
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
    require_application_permission(user, site, 'backup.view')
    return send_file(get_backup_path(site, backup_name), as_attachment=True, download_name=f'{site.name}-{backup_name}')


@app.route('/site/<int:site_id>/backup/<backup_name>/restore', methods=['POST'])
def restore_site_backup(site_id, backup_name):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    site = db.session.get(Site, site_id)
    require_application_permission(user, site, 'deployment.request')
    archive_path = get_backup_path(site, backup_name)
    access = ensure_application_access(site)
    site_path = application_root(access, bucket='file')
    try:
        restore_size = estimate_zip_unpacked_bytes(archive_path)
        enforce_application_quota(access, restore_size)
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
    require_application_permission(user, site, 'site.view')
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
    require_application_permission(user, site, 'files.delete')

    access = ensure_application_access(site)
    site_path = application_root(access, bucket='file')
    if os.path.exists(site_path):
        try:
            create_backup_archive(site, access=access)
        except ValueError:
            pass
        remove_tree(site_path)
        
    site_name = site.name
    access = ApplicationAccess.query.filter_by(site_id=site.id).first()
    if access:
        db.session.delete(access)
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
    require_application_permission(user, site, 'site.view')
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
@developer_required
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
    containers = list_docker_containers()
    return render_template('developer_docker.html', containers=containers)


@app.route('/developer/notifications', methods=['GET', 'POST'])
@developer_required
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
@developer_required
def developer_deploy_history():
    user = db.session.get(User, session['user_id'])
    events = DeploymentEvent.query.order_by(DeploymentEvent.created_at.desc()).limit(200).all()
    if user_role(user) != 'admin' and not user.is_admin:
        allowed_names = {site.name for site in Site.query.filter(Site.id.in_(assigned_application_ids(user))).all()}
        events = [event for event in events if event.site_name in allowed_names]
    else:
        events = events[:50]
    return render_template('developer_deploy_history.html', events=events)


@app.route('/developer/deploy', methods=['GET', 'POST'])
@developer_required
def developer_deploy():
    user = db.session.get(User, session['user_id'])
    role = user_role(user)
    allowed_site_ids = set(assigned_application_ids(user)) if role != 'admin' and not user.is_admin else None
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
            if allowed_site_ids is not None and site.id not in allowed_site_ids:
                abort(403)
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
        if allowed_site_ids is not None and site.id not in allowed_site_ids:
            abort(403)
        require_application_permission(user, site, 'deployment.execute')
        access = ensure_application_access(site)
        site_path = application_root(access, bucket='deployment')
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
            try:
                extract_zip_to_site(temp_path, site_path)
            except zipfile.BadZipFile as exc:
                flash(f'Invalid archive: {exc}', 'error')
                return redirect(url_for('developer_deploy'))
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            record_deployment_event(site.name, deploy_mode='zip', status='success', detail=f'ZIP deployment completed: {uploaded.filename}', repo_url=None)
            record_upload_history(site, user, uploaded.filename, 0, source='panel', status='success', deployment='deploy-zip', detail='zip deployment')
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
    if not user or not site:
        return abort(404)
    access = require_application_permission(user, site, 'files.delete')
    site_root = application_root(access, bucket='file')
        
    file_to_delete = request.form.get('file_path')
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
