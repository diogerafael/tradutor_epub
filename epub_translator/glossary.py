import os


def load_glossary(path):
    entries = []
    if not path or not os.path.exists(path):
        return entries
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if "=" in s:
                src, dst = s.split("=", 1)
                src = src.strip()
                dst = dst.strip()
                if src and dst:
                    entries.append((src, dst))
    return entries


def render(entries):
    return "\n".join(f'- "{src}" -> "{dst}"' for src, dst in entries)
