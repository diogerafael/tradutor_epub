import json
import random
import re
import socket
import time
import urllib.error
import urllib.request

from .prompt import build_system, build_batch_user, build_single_user

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_PLACEHOLDER_RE = re.compile(r"\[\[(\d+)\]\]")


class TranslationError(Exception):
    pass


def parse_json(content):
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    raise TranslationError("nao foi possivel interpretar o JSON da resposta")


class DeepSeekClient:
    def __init__(self, cfg, glossary_text, prompt_text):
        base = cfg.base_url.rstrip("/")
        self.endpoint = cfg.endpoint or (base + "/chat/completions")
        self.model = cfg.model
        self.key = cfg.api_key
        self.timeout = cfg.timeout_seconds
        self.max_retries = cfg.MAX_RETRIES
        self.temperature = cfg.temperature
        self.max_tokens = cfg.max_output_tokens
        self.src = cfg.source_lang
        self.tgt = cfg.target_lang
        self.system = build_system(prompt_text, glossary_text, self.src, self.tgt)

    def _sleep(self, attempt):
        time.sleep(min(60, 2 ** attempt) + random.random())

    @staticmethod
    def _read_error_body(e):
        try:
            return e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            return ""

    def _post(self, payload):
        last = None
        for attempt in range(self.max_retries + 1):
            try:
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(self.endpoint, data=data, method="POST")
                req.add_header("Content-Type", "application/json")
                req.add_header("Authorization", "Bearer " + self.key)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                body = self._read_error_body(e)
                if e.code in RETRYABLE_STATUS:
                    last = f"HTTP {e.code}: {body}"
                    self._sleep(attempt)
                    continue
                raise TranslationError(f"HTTP {e.code}: {body}") from e
            except (urllib.error.URLError, TimeoutError, ConnectionError,
                    socket.timeout, OSError) as e:
                last = f"{type(e).__name__}: {e}"
                self._sleep(attempt)
                continue
        raise TranslationError(f"falha apos {self.max_retries + 1} tentativas: {last}")

    def _chat(self, messages, json_mode=False):
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        resp = self._post(payload)
        try:
            return resp["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise TranslationError(f"resposta inesperada: {resp}") from e

    def translate_one(self, text, missing=None):
        messages = [
            {"role": "system", "content": self.system},
            {"role": "user", "content": build_single_user(text, self.src, self.tgt, missing)},
        ]
        return self._chat(messages).strip()

    def translate_batch(self, items):
        user = build_batch_user(items, self.src, self.tgt)
        messages = [
            {"role": "system", "content": self.system},
            {"role": "user", "content": user},
        ]
        content = self._chat(messages, json_mode=True)
        parsed = parse_json(content)
        if not isinstance(parsed, dict):
            raise TranslationError("resposta JSON nao e um objeto")
        translations = parsed.get("translations")
        if not isinstance(translations, list) or len(translations) != len(items):
            raise TranslationError("numero de traducoes nao confere")
        return translations


def _placeholders(text):
    return set(_PLACEHOLDER_RE.findall(text))


def _preserved(original, translated):
    return _placeholders(original) <= _placeholders(translated)


def _missing_placeholders(original, translated):
    return sorted(_placeholders(original) - _placeholders(translated), key=int)


def translate_chunk(client, chunk, batch_attempts=3):
    items = [p["masked"] for p in chunk]
    batch_errors = []

    for _ in range(batch_attempts):
        try:
            out = client.translate_batch(items)
            if all(_preserved(o, t) for o, t in zip(items, out)):
                return out
            for o, t in zip(items, out):
                miss = _missing_placeholders(o, t)
                if miss:
                    batch_errors.append(f"placeholders ausentes ({', '.join(miss)}): {t[:60]!r}")
        except TranslationError as e:
            batch_errors.append(str(e))

    result = []
    item_errors = []
    for it in items:
        missing = None
        done = False
        for _ in range(batch_attempts):
            try:
                t = client.translate_one(it, missing=missing)
            except TranslationError as e:
                item_errors.append(str(e))
                continue
            miss = _missing_placeholders(it, t)
            if not miss:
                result.append(t)
                done = True
                break
            missing = miss
            item_errors.append(f"placeholders ausentes ({', '.join(miss)}): {t[:60]!r}")
        if not done:
            reasons = batch_errors + item_errors
            detail = "; ".join(reasons[-4:]) if reasons else "sem detalhes"
            raise TranslationError(
                f"falha ao traduzir segmento: {it[:120]!r} ({detail})"
            )
    return result
