import os

DEFAULT_PROMPT = (
    "You are an expert translator of technical books. "
    "Translate the provided text from English to Brazilian Portuguese. "
    "Preserve meaning, do not summarize or add information, use natural Portuguese, "
    "preserve technical terms from the glossary, proper names, code, numbers, URLs, "
    "and references. Keep any [[number]] token exactly as-is. Output only the translation."
)


def load_prompt(path):
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    return DEFAULT_PROMPT


def build_system(prompt_text, glossary_text, source_lang, target_lang):
    parts = [prompt_text]
    if glossary_text:
        parts.append(
            f"\nGlossary (must be applied consistently; keep the translation on the right side):\n{glossary_text}"
        )
    return "\n".join(parts)


def build_batch_user(items, source_lang, target_lang):
    n = len(items)
    lines = [
        f"Translate each of the {n} text segments below from {source_lang} to {target_lang}.",
        "Keep every [[number]] token exactly as-is (same position and order).",
        "Do not translate code, URLs, numbers, or proper names.",
        'Return ONLY a valid JSON object with a single key "translations" whose value '
        f"is an array of exactly {n} strings, in the same order as the segments.",
        "",
        "Segments:",
    ]
    for i, it in enumerate(items, 1):
        lines.append(f"[{i}] {it}")
    return "\n".join(lines)


def build_single_user(text, source_lang, target_lang, missing=None):
    hint = ""
    if missing:
        hint = (
            "\n\nIMPORTANT: your previous translation omitted these tokens: "
            f"{', '.join(missing)}. Every [[number]] token from the source text "
            "must appear exactly once in the output, in the same order."
        )
    return (
        f"Translate the following text from {source_lang} to {target_lang}. "
        "Keep every [[number]] token exactly as-is. Do not translate code, URLs, "
        "numbers, or proper names. Return only the translation." + hint + "\n\n"
        f"Text:\n{text}"
    )
