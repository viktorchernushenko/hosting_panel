"""Single source of truth for user-visible MyH runtime capabilities."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path


RUNTIME_DEFINITIONS = (
    {
        "id": "static", "displayName": {"uk": "Статичний сайт", "en": "Static site"},
        "description": {"uk": "HTML, CSS, JavaScript та готові frontend-збірки.", "en": "HTML, CSS, JavaScript and compiled frontend builds."},
        "version": "nginx-alpine", "template": "nginx:1.27-alpine", "category": "core", "icon": "globe",
        "supportsBuild": False, "supportsDatabase": False, "supportsEnvironmentVariables": False,
        "supportsCustomStartCommand": False, "supportsSpa": True, "advanced": False,
        "recommendedFor": ["HTML/CSS/JS", "React build", "Vue build", "Angular build", "Vite build"],
    },
    {
        "id": "php", "displayName": {"uk": "PHP", "en": "PHP"},
        "description": {"uk": "PHP-сайти, CMS та вебзастосунки.", "en": "PHP sites, CMS platforms and web applications."},
        "version": "8.2", "template": "myh-stack-php:8.2", "category": "core", "icon": "code",
        "supportsBuild": False, "supportsDatabase": True, "supportsEnvironmentVariables": True,
        "supportsCustomStartCommand": False, "supportsSpa": False, "advanced": False,
        "recommendedFor": ["WordPress*", "Laravel*", "Symfony*", "Custom PHP"],
    },
    {
        "id": "wordpress", "displayName": {"uk": "WordPress", "en": "WordPress"},
        "description": {"uk": "WordPress-сайт з автоматично налаштованою базою даних.", "en": "A WordPress site with an automatically configured database."},
        "version": "6-php8.3-fpm-alpine", "template": "wordpress:6-php8.3-fpm-alpine", "category": "managed", "icon": "globe",
        "supportsBuild": False, "supportsDatabase": True, "supportsEnvironmentVariables": True,
        "supportsCustomStartCommand": False, "supportsSpa": False, "advanced": False,
        "recommendedFor": ["Blog", "Business site", "CMS", "WooCommerce*"],
    },
    {
        "id": "node", "displayName": {"uk": "Node.js", "en": "Node.js"},
        "description": {"uk": "Серверні JavaScript та TypeScript застосунки.", "en": "Server-side JavaScript and TypeScript applications."},
        "version": "22", "template": "node:22-alpine", "category": "core", "icon": "code",
        "supportsBuild": True, "supportsDatabase": True, "supportsEnvironmentVariables": True,
        "supportsCustomStartCommand": True, "supportsSpa": False, "advanced": False,
        "recommendedFor": ["Express*", "NestJS*", "Next.js server*"],
    },
    {
        "id": "python", "displayName": {"uk": "Python", "en": "Python"},
        "description": {"uk": "Python вебзастосунки.", "en": "Python web applications."},
        "version": "3.12", "template": "myh-stack-python-web:3.12", "category": "core", "icon": "code",
        "supportsBuild": True, "supportsDatabase": True, "supportsEnvironmentVariables": True,
        "supportsCustomStartCommand": True, "supportsSpa": False, "advanced": False,
        "recommendedFor": ["Django*", "Flask*", "FastAPI*"],
    },
    {
        "id": "docker", "displayName": {"uk": "Docker", "en": "Docker"},
        "description": {"uk": "Власний Dockerfile та нестандартні технології.", "en": "A custom Dockerfile and non-standard technologies."},
        "version": "engine", "template": "Docker Engine", "category": "advanced", "icon": "box",
        "supportsBuild": True, "supportsDatabase": True, "supportsEnvironmentVariables": True,
        "supportsCustomStartCommand": False, "supportsSpa": False, "advanced": True,
        "recommendedFor": ["Go", "Java", ".NET", "Ruby", "Custom stack"],
    },
)
_CATALOG_CACHE: dict[tuple, tuple[float, list[dict]]] = {}


def _installed(definition: dict) -> tuple[bool, str]:
    if definition["id"] == "docker":
        command = ["docker", "version", "--format", "{{.Server.Version}}"]
    else:
        command = ["docker", "image", "inspect", definition["template"]]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)[:160]
    detail = result.stdout.strip() if definition["id"] == "docker" else definition["template"]
    return result.returncode == 0, detail


def runtime_catalog(config_path: str, health_path: str, language: str = "uk", testing: bool = False) -> list[dict]:
    key = (
        config_path, health_path, language, testing,
        os.path.getmtime(config_path) if os.path.exists(config_path) else None,
        os.path.getmtime(health_path) if os.path.exists(health_path) else None,
    )
    cached = _CATALOG_CACHE.get(key)
    if cached and time.monotonic() - cached[0] < 30:
        return [dict(row) for row in cached[1]]
    try:
        config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        config = {}
    try:
        report = json.loads(Path(health_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        report = {}
    verified = {row.get("runtime"): row for row in report.get("results", []) if isinstance(row, dict)}
    generated_at = report.get("generated_at")
    rows = []
    for definition in RUNTIME_DEFINITIONS:
        runtime_id = definition["id"]
        installed, detail = (True, definition["template"]) if testing else _installed(definition)
        enabled = bool(config.get(f"{runtime_id}:{definition['version']}", {}).get("enabled", True))
        verification = verified.get(runtime_id, {})
        e2e_ok = testing or verification.get("status") == "AVAILABLE"
        if not enabled or not installed:
            status = "UNAVAILABLE"
        elif e2e_ok:
            status = "AVAILABLE"
        else:
            status = "NOT_TESTED"
        row = dict(definition)
        row.update({
            "name": definition["displayName"].get(language, definition["displayName"]["uk"]),
            "description": definition["description"].get(language, definition["description"]["uk"]),
            "available": status == "AVAILABLE", "status": status, "installed": installed,
            "enabled": enabled, "health": "healthy" if e2e_ok else "unknown",
            "lastTest": generated_at, "detail": detail, "test": verification,
        })
        rows.append(row)
    _CATALOG_CACHE.clear()
    _CATALOG_CACHE[key] = (time.monotonic(), rows)
    return [dict(row) for row in rows]


def public_runtime(row: dict) -> dict:
    return {key: row[key] for key in (
        "id", "name", "description", "available", "status", "version", "category", "icon",
        "supportsBuild", "supportsDatabase", "supportsEnvironmentVariables",
        "supportsCustomStartCommand", "supportsSpa", "advanced", "recommendedFor", "lastTest",
    )}
