import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import epub_io
from .api import DeepSeekClient, TranslationError, translate_chunk
from .cache import Cache, cache_key, chunk_hash
from .glossary import load_glossary, render
from .html import (
    chunk_pieces,
    est_tokens,
    extract_document,
    prepare_pieces,
    reconstruct,
    unmask,
)
from .prompt import load_prompt


def load_context(cfg):
    glossary_entries = load_glossary(cfg.glossary_file)
    glossary_text = render(glossary_entries)
    prompt_text = load_prompt(cfg.prompt_file)
    return prompt_text, glossary_text, glossary_entries


def extract_report(zf, content_files, cfg):
    report = {"files": [], "total_segments": 0, "total_pieces": 0,
              "total_chunks": 0, "total_est_tokens": 0}
    samples = []
    for name in content_files:
        raw = zf.read(name)
        html = raw.decode("utf-8", errors="replace")
        doc = extract_document(html)
        n_segs = len(doc["segs"])
        pieces = prepare_pieces(doc["segs"], cfg.MAX_CHUNK_TOKENS)
        chunks = chunk_pieces(pieces, cfg.MAX_CHUNK_TOKENS)
        toks = sum(est_tokens(p["masked"]) for p in pieces)
        report["files"].append({
            "name": name,
            "segments": n_segs,
            "pieces": len(pieces),
            "chunks": len(chunks),
            "est_tokens": toks,
        })
        report["total_segments"] += n_segs
        report["total_pieces"] += len(pieces)
        report["total_chunks"] += len(chunks)
        report["total_est_tokens"] += toks
        if len(samples) < 5 and pieces:
            samples.append({"file": name, "sample": pieces[0]["masked"][:160]})
    report["samples"] = samples
    report["images"] = sorted(epub_io.image_hashes(zf).keys())
    report["image_count"] = len(report["images"])
    return report


_STATS_LOCK = threading.Lock()


def _translate_one_chunk(name, ci, key, expected_hash, chunk, client, cache, stats):
    try:
        results = translate_chunk(client, chunk)
        cache.save(key, {"file": name, "chunk": ci,
                         "input_hash": expected_hash, "items": results})
        with _STATS_LOCK:
            stats["chunks_translated"] += 1
        return results
    except TranslationError as e:
        msg = f"{name} (chunk {ci}): {e}"
        with _STATS_LOCK:
            stats["chunks_failed"] += 1
            stats["errors"].append(msg)
        print(f"AVISO: falha ao traduzir, mantendo original: {msg}",
              file=sys.stderr, flush=True)
        return [p["masked"] for p in chunk]


def translate_chunks(name, chunks, client, cache, stats, workers=1, dry_run=False):
    """Traduz os chunks de um arquivo, reaproveitando o cache.

    Com workers > 1, os chunks pendentes sao enviados em paralelo. Nesse
    escopo as chaves de cache sao unicas, entao nao ha corrida na escrita.
    """
    results = [None] * len(chunks)
    pending = []

    for ci, chunk in enumerate(chunks):
        key = cache_key(name, ci)
        h = chunk_hash(chunk)
        cached = cache.load(key)
        if cached and cached.get("input_hash") == h and len(cached.get("items", [])) == len(chunk):
            results[ci] = cached["items"]
            stats["chunks_cached"] += 1
        elif dry_run:
            results[ci] = [p["masked"] for p in chunk]
            stats["chunks_translated"] += 1
        else:
            pending.append((ci, key, h, chunk))

    if not pending:
        return results

    if workers <= 1:
        for ci, key, h, chunk in pending:
            results[ci] = _translate_one_chunk(name, ci, key, h, chunk,
                                               client, cache, stats)
        return results

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_translate_one_chunk, name, ci, key, h, chunk,
                        client, cache, stats): ci
            for ci, key, h, chunk in pending
        }
        for fut in as_completed(futures):
            ci = futures[fut]
            results[ci] = fut.result()
            print(f"  {name} chunk {ci + 1}/{len(chunks)}", file=sys.stderr, flush=True)
    return results


def translate(zf, content_files, cfg, out_path=None, dry_run=False, workers=1):
    prompt_text, glossary_text, _ = load_context(cfg)
    client = None if dry_run else DeepSeekClient(cfg, glossary_text, prompt_text)
    cache = Cache(cfg.cache_dir)
    translated = {}
    stats = {"chunks_cached": 0, "chunks_translated": 0, "chunks_failed": 0,
             "segments": 0, "errors": []}

    for name in content_files:
        original_bytes = zf.read(name)
        html = original_bytes.decode("utf-8", errors="replace")
        doc = extract_document(html)
        if not doc["segs"]:
            translated[name] = original_bytes
            continue

        pieces = prepare_pieces(doc["segs"], cfg.MAX_CHUNK_TOKENS)
        chunks = chunk_pieces(pieces, cfg.MAX_CHUNK_TOKENS)
        stats["segments"] += len(doc["segs"])

        results = translate_chunks(name, chunks, client, cache, stats,
                                   workers=workers, dry_run=dry_run)

        for chunk, chunk_results in zip(chunks, results):
            for p, r in zip(chunk, chunk_results):
                p["translated"] = r

        for seg in doc["segs"]:
            for p in seg["pieces"]:
                p["fragment"] = unmask(p["translated"], p["placeholders"])

        translated[name] = reconstruct(doc).encode("utf-8")

    if not dry_run and out_path:
        epub_io.write_epub(zf, translated, out_path)
        img_count = epub_io.validate_images(zf, out_path)
        stats["images_validated"] = img_count

    return translated, stats


def print_report(report):
    print(f"Arquivos de conteudo: {len(report['files'])}")
    print(f"Segmentos traduziveis: {report['total_segments']}")
    print(f"Blocos (pieces): {report['total_pieces']}")
    print(f"Chunks de API: {report['total_chunks']}")
    print(f"Tokens estimados: {report['total_est_tokens']}")
    print(f"Imagens preservadas: {report['image_count']}")
    print()
    print(f"{'ARQUIVO':32} {'SEG':>5} {'PIECES':>7} {'CHUNKS':>7} {'TOKENS':>8}")
    for f in report["files"]:
        print(f"{f['name']:32} {f['segments']:>5} {f['pieces']:>7} "
              f"{f['chunks']:>7} {f['est_tokens']:>8}")
    if report.get("samples"):
        print("\nAmostras de segmentos mascarados:")
        for s in report["samples"]:
            print(f"  [{s['file']}] {s['sample']}")
