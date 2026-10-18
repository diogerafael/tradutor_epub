import json
import os
from types import SimpleNamespace

DEFAULTS = {
    "base_url": "https://api.deepseek.com",
    "endpoint": "",
    "model": "deepseek-chat",
    "api_key_env": "DEEPSEEK_API_KEY",
    "api_key": "",
    "MAX_CHUNK_TOKENS": 3000,
    "MAX_RETRIES": 5,
    "timeout_seconds": 120,
    "temperature": 0.2,
    "max_output_tokens": 4096,
    "retry_rounds": 2,
    "source_lang": "English",
    "target_lang": "Brazilian Portuguese",
    "glossary_file": "glossary.txt",
    "prompt_file": "prompts/translation_prompt.txt",
    "cache_dir": ".cache",
}


def _resolve(path, cfg_path):
    if not path or os.path.isabs(path):
        return path
    base = os.path.dirname(os.path.abspath(cfg_path)) if cfg_path else os.getcwd()
    return os.path.normpath(os.path.join(base, path))


def load_config(path=None):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg.update(json.load(f))

    env_name = cfg.get("api_key_env") or "DEEPSEEK_API_KEY"
    cfg["api_key"] = os.environ.get(env_name, cfg.get("api_key") or "")

    cfg["glossary_file"] = _resolve(cfg["glossary_file"], path)
    cfg["prompt_file"] = _resolve(cfg["prompt_file"], path)
    cfg["cache_dir"] = _resolve(cfg["cache_dir"], path)

    return SimpleNamespace(**cfg)
