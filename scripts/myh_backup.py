#!/usr/bin/env python3
"""Create a verified local MyH backup, then independently verify a remote copy."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

LOCAL_ROOT = Path(os.environ.get('BACKUP_LOCAL_ROOT', '/srv/backups/myh'))
STATUS_PATH = Path(os.environ.get('BACKUP_STATUS_PATH', '/var/lib/myh-backup/status.json'))
REMOTE_TYPE = os.environ.get('BACKUP_REMOTE_TYPE', 'none').strip().lower()
REMOTE_TARGET = os.environ.get('BACKUP_REMOTE_TARGET', '').strip()
RETENTION_DAYS = max(1, int(os.environ.get('BACKUP_RETENTION_DAYS', '14')))
DB_PATH = Path('/home/myserver/hosting_panel/instance/hosting.db')


def checksum(path: Path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n')
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def run(args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)


def sqlite_backup(destination: Path):
    source = sqlite3.connect(DB_PATH, timeout=30)
    target = sqlite3.connect(destination)
    with target:
        source.backup(target)
    integrity = target.execute('PRAGMA integrity_check').fetchone()[0]
    counts = {table: target.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
              for table in ('user', 'site', 'audit_log', 'database_resource')}
    target.close(); source.close()
    if integrity != 'ok':
        raise RuntimeError('SQLite backup integrity verification failed')
    return counts


def remote_copy(source: Path, digest: str, backup_id: str):
    if REMOTE_TYPE == 'none' or not REMOTE_TARGET:
        return 'not_configured', '', 'remote storage configuration required'
    if REMOTE_TYPE == 'local_mount':
        target_root = Path(REMOTE_TARGET).resolve()
        run(['findmnt', '--mountpoint', str(target_root)])
        destination = target_root / backup_id / source.name
        destination.parent.mkdir(parents=True, exist_ok=False)
        shutil.copy2(source, destination)
        return ('verified' if checksum(destination) == digest else 'failed', str(destination), 'checksum verified')
    if REMOTE_TYPE == 'sftp':
        host = os.environ.get('BACKUP_REMOTE_HOST', '').strip()
        identity = os.environ.get('BACKUP_SSH_IDENTITY_FILE', '').strip()
        port = os.environ.get('BACKUP_REMOTE_PORT', '22')
        if not host or not identity:
            return 'not_configured', '', 'SFTP host or identity file missing'
        remote_dir = REMOTE_TARGET.rstrip('/') + '/' + backup_id
        ssh = ['ssh', '-oBatchMode=yes', '-oStrictHostKeyChecking=yes', '-i', identity, '-p', port, host]
        run(ssh + ['mkdir', '-p', remote_dir])
        run(['scp', '-q', '-oBatchMode=yes', '-oStrictHostKeyChecking=yes', '-i', identity, '-P', port, str(source), f'{host}:{remote_dir}/'])
        completed = subprocess.run(ssh + ['sha256sum', f'{remote_dir}/{source.name}'], check=True, capture_output=True, text=True)
        remote_digest = completed.stdout.split()[0] if completed.stdout.split() else ''
        return ('verified' if remote_digest == digest else 'failed', f'sftp:{remote_dir}/{source.name}', 'checksum verified')
    return 'not_configured', '', f'unsupported remote type: {REMOTE_TYPE}'


def main():
    backup_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    destination = LOCAL_ROOT / backup_id
    destination.mkdir(parents=True, mode=0o700)
    try:
        previous_restore_test = json.loads(STATUS_PATH.read_text()).get('last_restore_test')
    except (OSError, ValueError):
        previous_restore_test = None
    status = {'backup_id': backup_id, 'timestamp': datetime.now(timezone.utc).isoformat(), 'local_status': 'creating',
              'remote_status': 'pending', 'remote_type': REMOTE_TYPE, 'remote_location': '', 'last_restore_test': previous_restore_test}
    try:
        counts = sqlite_backup(destination / 'hosting.db')
        archive = destination / 'myh-data.tar.gz'
        run(['tar', '--xattrs', '--acls', '-czf', str(archive),
             '--exclude=__pycache__', '--exclude=*.log', '--exclude=tmp',
             '--exclude=hosting.db', '--exclude=hosting.db-wal', '--exclude=hosting.db-shm',
             '-C', '/', 'home/myserver/hosting_panel/user_sites', 'home/myserver/hosting_panel/instance',
             'srv/apps', 'srv/backups/mysql', 'etc/ssh/sshd_config.d', 'etc/cloudflared/config.yml',
             'etc/systemd/system/myh-guru.service', 'etc/systemd/system/myh-guru.service.d'])
        bundle = destination / 'myh-backup.tar'
        run(['tar', '-cf', str(bundle), '-C', str(destination), 'hosting.db', 'myh-data.tar.gz'])
        digest = checksum(bundle)
        status.update(local_status='verified', size=bundle.stat().st_size, checksum=digest,
                      sqlite_checksum=checksum(destination / 'hosting.db'), data_checksum=checksum(archive),
                      sqlite_integrity='ok', sqlite_counts=counts)
        remote_source, remote_digest = bundle, digest
        if REMOTE_TYPE != 'none' and REMOTE_TARGET:
            recipient = os.environ.get('BACKUP_GPG_RECIPIENT', '').strip()
            if not recipient:
                raise RuntimeError('remote backup encryption recipient is not configured')
            remote_source = destination / 'myh-backup.tar.gpg'
            run(['gpg', '--batch', '--yes', '--trust-model', 'always', '--recipient', recipient,
                 '--output', str(remote_source), '--encrypt', str(archive)])
            remote_digest = checksum(remote_source)
            status.update(encryption='gpg-public-key', remote_checksum=remote_digest)
        remote_status, remote_location, detail = remote_copy(remote_source, remote_digest, backup_id)
        status.update(remote_status=remote_status, remote_location=remote_location, detail=detail)
        write_json(destination / 'manifest.json', status)
        write_json(STATUS_PATH, status)
        cutoff = datetime.now(timezone.utc).timestamp() - RETENTION_DAYS * 86400
        for item in LOCAL_ROOT.iterdir():
            if item.is_dir() and item != destination and item.stat().st_mtime < cutoff:
                shutil.rmtree(item)
        print(f'LOCAL_BACKUP={status["local_status"]} REMOTE_BACKUP={status["remote_status"]}')
        return 0 if remote_status == 'verified' else 2
    except Exception as exc:
        status.update(local_status='failed', remote_status='failed', detail=str(exc)[:300])
        write_json(STATUS_PATH, status)
        print('LOCAL_BACKUP=failed REMOTE_BACKUP=failed', file=sys.stderr)
        return 1


def restore_test():
    candidates = sorted((item for item in LOCAL_ROOT.iterdir() if item.is_dir()), reverse=True)
    if not candidates:
        print('RESTORE_TEST=failed no backup available', file=sys.stderr)
        return 1
    source = candidates[0]
    manifest_path, database = source / 'manifest.json', source / 'hosting.db'
    archive, bundle = source / 'myh-data.tar.gz', source / 'myh-backup.tar'
    manifest = json.loads(manifest_path.read_text())
    if checksum(bundle) != manifest.get('checksum') or checksum(database) != manifest.get('sqlite_checksum'):
        raise RuntimeError('backup checksum mismatch')
    with tempfile.TemporaryDirectory(prefix='myh-restore-test-') as temporary:
        disposable_db = Path(temporary) / 'hosting.db'
        shutil.copy2(database, disposable_db)
        connection = sqlite3.connect(disposable_db)
        integrity = connection.execute('PRAGMA integrity_check').fetchone()[0]
        counts = {table: connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                  for table in ('user', 'site', 'audit_log', 'database_resource')}
        connection.close()
        run(['tar', '-tf', str(bundle)])
        run(['tar', '-tzf', str(archive)])
    if integrity != 'ok' or counts != manifest.get('sqlite_counts'):
        raise RuntimeError('disposable SQLite restore verification failed')
    tested_at = datetime.now(timezone.utc).isoformat()
    manifest['last_restore_test'] = {'timestamp': tested_at, 'status': 'verified', 'source': 'local'}
    write_json(manifest_path, manifest)
    write_json(STATUS_PATH, manifest)
    print('RESTORE_TEST=verified SOURCE=local')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(restore_test() if '--restore-test' in sys.argv else main())
    except Exception as exc:
        print(f'RESTORE_TEST=failed {str(exc)[:200]}', file=sys.stderr)
        raise SystemExit(1)
