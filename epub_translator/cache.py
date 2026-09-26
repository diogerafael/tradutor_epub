import json
import os
import hashlib


class Cache:
    def __init__(self, cache_dir):
        self.dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def _path(self, key):
        return os.path.join(self.dir, key + ".json")

    def load(self, key):
        p = self._path(key)
        if not os.path.exists(p):
            return None
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def save(self, key, data):
        p = self._path(key)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, p)


def chunk_hash(chunk):
    h = hashlib.sha256()
    for p in chunk:
        h.update(p["masked"].encode("utf-8"))
        h.update(b"\x00")
        for ph in p["placeholders"]:
            h.update(ph.encode("utf-8"))
            h.update(b"\x01")
        h.update(b"\x02")
    return h.hexdigest()


def cache_key(file_name, chunk_index):
    stem = os.path.splitext(os.path.basename(file_name))[0]
    stem = "".join(c if c.isalnum() or c in "-_." else "_" for c in stem)
    return f"{stem}_chunk_{chunk_index:03d}"
