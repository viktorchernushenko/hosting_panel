#!/usr/bin/env python3
"""Destructive-to-temporary-data lifecycle verification for managed runtimes."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from runtime_engine import compose_action, healthcheck, prepare_custom_docker, prepare_runtime


def write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=5) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        return response.read().decode("utf-8", errors="replace")


def compose(metadata: dict, *args: str) -> subprocess.CompletedProcess:
    stack = Path(metadata["stack_root"])
    command = ["docker", "compose", "-p", metadata["project"]]
    for compose_file in metadata.get("compose_files") or [str(stack / "compose.yml")]:
        command.extend(["-f", compose_file])
    if metadata.get("project_directory"):
        command.extend(["--project-directory", metadata["project_directory"]])
    return subprocess.run([*command, *args], capture_output=True, text=True, timeout=180, check=False)


def fixture(runtime: str, source: Path) -> None:
    if runtime == "static":
        write(source / "index.html", '<link rel="stylesheet" href="styles.css"><h1 id="result">Static E2E</h1><script src="app.js"></script>')
        write(source / "styles.css", "body{color:rgb(1,2,3)}")
        write(source / "app.js", "document.querySelector('#result').dataset.ok='yes';")
        write(source / "404.html", "Static custom 404")
    elif runtime == "php":
        write(source / "index.php", '<?php $required=["pdo_mysql","mysqli","mbstring","curl","openssl","fileinfo","json","xml","zip","gd","intl"]; $missing=array_filter($required,fn($e)=>!extension_loaded($e)); echo $missing ? "PHP E2E:MISSING:".implode(",",$missing) : "PHP E2E:OK:".PHP_VERSION;')
    elif runtime == "node":
        write(source / "package.json", json.dumps({"scripts": {"start": "node server.js"}}))
        write(source / "server.js", "const http=require('http');http.createServer((q,r)=>r.end(q.url==='/health'?'ok':'Node E2E')).listen(process.env.PORT||8080,'0.0.0.0');")
    elif runtime == "python":
        write(source / "app.py", "def app(environ,start_response):\n start_response('200 OK',[('Content-Type','text/plain')]); return [b'Python E2E']\n")
    elif runtime == "docker":
        write(source / "index.html", "Docker E2E")
        write(source / "default.conf", "server { listen 8080; root /usr/share/nginx/html; index index.html; location / { try_files $uri $uri/ =404; } }")
        write(source / "Dockerfile", "FROM nginx:1.27-alpine\nCOPY index.html /usr/share/nginx/html/index.html\nCOPY default.conf /etc/nginx/conf.d/default.conf\n")


def verify_runtime(runtime: str, base: Path) -> dict:
    source, stack = base / runtime / "source", base / runtime / "stack"
    source.mkdir(parents=True)
    stack.mkdir(parents=True)
    fixture(runtime, source)
    metadata = (
        prepare_custom_docker(str(stack), str(source))
        if runtime == "docker"
        else prepare_runtime(str(stack), str(source), runtime)
    )
    metadata["stack_root"] = str(stack)
    result = {key: False for key in ("create", "build", "start", "http", "health", "logs", "restart", "stop", "delete", "security")}
    result.update({"runtime": runtime, "version": metadata["version"], "error": ""})
    result["create"] = (stack / "runtime.json").is_file() and (stack / "compose.yml").is_file()
    try:
        code, output = compose_action(str(stack), "start", timeout=300)
        result["build"] = "error" not in output.lower() and code == 0
        result["start"] = code == 0
        if code:
            raise RuntimeError(output[-1000:])
        expected = {"static": "Static E2E", "php": "PHP E2E:OK", "node": "Node E2E", "python": "Python E2E", "docker": "Docker E2E"}[runtime]
        ready = healthcheck(metadata, timeout=30)
        result["health"] = ready.get("ok", False)
        if not result["health"]:
            raise RuntimeError(f"healthcheck failed: {ready}")
        body = get(f'http://127.0.0.1:{metadata["port"]}/')
        result["http"] = expected in body
        logs = compose(metadata, "logs", "--no-color", "--tail", "50")
        result["logs"] = logs.returncode == 0
        inspect = compose(metadata, "config", "--format", "json")
        config = json.loads(inspect.stdout)
        services = config.get("services", {})
        def loopback_only(service: dict) -> bool:
            for port in service.get("ports", []):
                if isinstance(port, dict):
                    if port.get("host_ip") != "127.0.0.1":
                        return False
                elif not str(port).startswith("127.0.0.1:"):
                    return False
            return True

        result["security"] = bool(services) and all(
            not service.get("privileged")
            and service.get("network_mode") != "host"
            and not service.get("devices")
            and service.get("pids_limit")
            and service.get("mem_limit")
            and loopback_only(service)
            for service in services.values()
        )
        code, _ = compose_action(str(stack), "restart")
        result["restart"] = code == 0 and healthcheck(metadata, timeout=20).get("ok", False)
        code, _ = compose_action(str(stack), "stop")
        time.sleep(0.5)
        result["stop"] = code == 0
        code, _ = compose_action(str(stack), "start", timeout=300)
        if code or not healthcheck(metadata, timeout=20).get("ok", False):
            raise RuntimeError("second start failed")
    except Exception as exc:
        result["error"] = str(exc)[-1000:]
    finally:
        try:
            code, _ = compose_action(str(stack), "delete")
            result["delete"] = code == 0 and not compose(metadata, "ps", "-q").stdout.strip()
        except Exception as exc:
            result["error"] = (result["error"] + "; cleanup: " + str(exc))[-1000:]
    result["status"] = "AVAILABLE" if all(result[key] for key in ("create", "build", "start", "http", "health", "logs", "restart", "stop", "delete", "security")) else "UNAVAILABLE"
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", help="Optional JSON report path")
    args = parser.parse_args()
    base = Path(tempfile.mkdtemp(prefix="myh-runtime-smoke-", dir="/tmp"))
    try:
        results = []
        for runtime in ("static", "php", "node", "python", "docker"):
            try:
                results.append(verify_runtime(runtime, base))
            except Exception as exc:
                results.append({"runtime": runtime, "status": "UNAVAILABLE", "error": str(exc)[-1000:]})
        payload = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "results": results}
        rendered = json.dumps(payload, indent=2)
        print(rendered)
        if args.output:
            Path(args.output).write_text(rendered + "\n", encoding="utf-8")
        return 0 if all(row["status"] == "AVAILABLE" for row in results) else 1
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
