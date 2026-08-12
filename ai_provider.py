"""Provider boundary for read-only MyH AI diagnostics."""
from __future__ import annotations

import json
import urllib.request


class AIProviderError(RuntimeError):
    pass


class LocalOpenAICompatibleProvider:
    def __init__(self, url: str, model: str, key_file: str, timeout: int = 120):
        self.url = url
        self.model = model
        self.key_file = key_file
        self.timeout = timeout

    def complete(self, messages: list[dict], max_tokens: int = 64) -> tuple[str, dict]:
        with open(self.key_file, encoding='utf-8') as handle:
            api_key = handle.read().strip()
        if not api_key:
            raise AIProviderError('AI credential is empty')
        body = json.dumps({
            'model': self.model, 'messages': messages, 'max_tokens': max_tokens,
            'temperature': 0.15, 'stream': False,
        }).encode('utf-8')
        request = urllib.request.Request(
            self.url, data=body, method='POST',
            headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}'},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            result = json.loads(response.read().decode('utf-8'))
        try:
            answer = str(result['choices'][0]['message']['content']).strip()[:2000]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProviderError('AI provider returned an invalid response') from exc
        usage = result.get('usage') or {}
        return answer, {
            'prompt_tokens': int(usage.get('prompt_tokens') or 0),
            'completion_tokens': int(usage.get('completion_tokens') or 0),
            'total_tokens': int(usage.get('total_tokens') or 0),
        }


def build_provider(provider: str, url: str, model: str, key_file: str):
    if provider == 'local-openai-compatible' and url and key_file:
        return LocalOpenAICompatibleProvider(url, model, key_file)
    return None
