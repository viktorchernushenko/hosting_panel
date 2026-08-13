#!/usr/bin/env python3
"""CLI entry point for deduplicated watchdog alerts."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from notification_service import NotificationService

parser = argparse.ArgumentParser()
parser.add_argument('--key', required=True)
parser.add_argument('--level', default='warning')
parser.add_argument('--resolved', action='store_true')
parser.add_argument('message')
args = parser.parse_args()
service = NotificationService.from_env('/var/lib/myh-notifications/state.json')
result = service.send(args.message, level=args.level, alert_key=args.key, resolved=args.resolved)
print('DELIVERED' if result['delivered'] else ('SUPPRESSED' if result['suppressed'] else 'NOT_CONFIGURED_OR_FAILED'))
