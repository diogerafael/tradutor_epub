import json
import random
import re
import socket
import sys
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
    for piece, item in zip(chunk, items):
        t, best, errors = _translate_item(client, piece, batch_attempts)
        item_errors.extend(errors)
        if t is None:
            t, errors = _translate_split(client, piece, batch_attempts)
            item_errors.extend(errors)
        if t is None:
            t = _reinsert_tokens(piece["masked"], best)
            if t is not None:
                print(f"AVISO: placeholders reposicionados por proporcao em: "
                      f"{item[:80]!r}", file=sys.stderr, flush=True)
        if t is None:
            reasons = batch_errors + item_errors
            detail = "; ".join(reasons[-4:]) if reasons else "sem detalhes"
            raise TranslationError(
                f"falha ao traduzir segmento: {item[:120]!r} ({detail})"
            )
        result.append(t)
    return result


def _translate_item(client, piece, attempts):
    """Traduz um segmento e guarda a melhor tentativa (menos placeholders perdidos)."""
    text = piece["masked"]
    best = None
    best_missing = None
    errors = []
    for _ in range(attempts):
        try:
            t = client.translate_one(text)
        except TranslationError as e:
            errors.append(str(e))
            continue
        miss = _missing_placeholders(text, t)
        if not miss:
            return t, t, errors
        if best_missing is None or len(miss) < len(best_missing):
            best, best_missing = t, miss
        errors.append(f"placeholders ausentes ({', '.join(miss)}): {t[:60]!r}")
    return None, best, errors


def _translate_split(client, piece, attempts):
    """Ultimo recurso: traduz o segmento dividido em sentencas.

    Segmentos longos com muitos placeholders costumam perder tokens. Sentencas
    curtas preservam melhor e a concatenacao mantem a ordem/originalidade dos
    placeholders, que sao renumerados para os indices do segmento original.
    """
    from .html import hard_split, remap_placeholders, split_into_sentences

    sentences = split_into_sentences(piece["masked"]) or [piece["masked"]]
    subpieces = []
    for sentence in sentences:
        for sub in (hard_split(sentence, 600) if len(sentence) > 600 else [sentence]):
            masked, placeholders = remap_placeholders(sub, piece["placeholders"])
            subpieces.append({"masked": masked, "placeholders": placeholders})
    if len(subpieces) < 2:
        return None, []

    errors = []
    parts = []
    for sub in subpieces:
        t, _, errs = _translate_item(client, sub, attempts)
        errors.extend(errs)
        if t is None:
            return None, errors
        parts.append((t, sub["placeholders"]))

    merged = []
    merged_ph = []
    for t, ph in parts:
        offset = len(merged_ph)

        def shift(m, offset=offset):
            return f"[[{int(m.group(1)) + offset}]]"

        merged.append(_PLACEHOLDER_RE.sub(shift, t))
        merged_ph.extend(ph)
    merged = " ".join(merged)

    # Volta aos indices originais, casando os placeholders em ordem.
    original = piece["placeholders"]
    pos = [0]

    def restore(m):
        tag = merged_ph[int(m.group(1))]
        for j in range(pos[0], len(original)):
            if original[j] == tag:
                pos[0] = j + 1
                return f"[[{j}]]"
        raise TranslationError("nao foi possivel remapear placeholders")

    try:
        restored = _PLACEHOLDER_RE.sub(restore, merged)
    except TranslationError as e:
        errors.append(str(e))
        return None, errors
    if not _preserved(piece["masked"], restored):
        errors.append("placeholders ausentes apos dividir em sentencas")
        return None, errors
    return restored, errors


def _snap_to_space(text, target):
    if target <= 0:
        return 0
    if target >= len(text):
        return len(text)
    left = text.rfind(" ", 0, target + 1)
    right = text.find(" ", target)
    if left == -1 and right == -1:
        return target
    if left == -1:
        return right
    if right == -1:
        return left
    return left if target - left <= right - target else right


def _reinsert_tokens(source_masked, translation):
    """Reinsere placeholders por posicao proporcional.

    Ultimo recurso apos esgotar as tentativas: usa o texto traduzido e coloca
    os tokens na posicao relativa que ocupavam no original. A ordem e mantida,
    entao o HTML continua balanceado; a formatacao inline pode ficar aproximada.
    """
    if not translation:
        return None

    tokens = []
    visible_src = 0
    last = 0
    for m in _PLACEHOLDER_RE.finditer(source_masked):
        visible_src += len(source_masked[last:m.start()])
        tokens.append((visible_src, m.group(0)))
        last = m.end()
    total_src = visible_src + len(source_masked[last:])
    if not tokens:
        return translation

    plain = _PLACEHOLDER_RE.sub("", translation)
    n = len(plain)
    if n == 0 or n < 0.6 * total_src:
        return None

    placed = []
    prev = 0
    for visible, token in tokens:
        target = round(visible / total_src * n) if total_src else 0
        offset = max(prev, _snap_to_space(plain, target))
        placed.append((offset, token))
        prev = offset

    parts = []
    prev = 0
    for offset, token in placed:
        parts.append(plain[prev:offset])
        parts.append(token)
        prev = offset
    parts.append(plain[prev:])
    result = "".join(parts)

    if [m.group(0) for m in _PLACEHOLDER_RE.finditer(result)] != [t for _, t in tokens]:
        return None
    return result
