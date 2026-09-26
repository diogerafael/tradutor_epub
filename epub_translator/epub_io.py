import hashlib
import os
import re
import zipfile
from xml.etree import ElementTree as ET

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".bmp", ".tif", ".tiff"}
XHTML_EXT = {".xhtml", ".html", ".htm"}


def local(tag):
    return tag.split("}")[-1]


def read_container(zf):
    try:
        data = zf.read("META-INF/container.xml").decode("utf-8", errors="replace")
    except KeyError:
        return "OEBPS/content.opf"
    root = ET.fromstring(data)
    for rf in root.iter():
        if local(rf.tag) == "rootfile":
            fp = rf.get("full-path")
            if fp:
                return fp
    return "OEBPS/content.opf"


def read_opf(zf, opf_path):
    data = zf.read(opf_path).decode("utf-8", errors="replace")
    root = ET.fromstring(data)
    base = os.path.dirname(opf_path)

    manifest = {}
    spine = []
    for el in root.iter():
        ln = local(el.tag)
        if ln == "item":
            manifest[el.get("id")] = el.get("href")
        elif ln == "itemref":
            spine.append(el.get("idref"))

    content = []
    seen = set()

    def add(href):
        if not href:
            return
        p = os.path.normpath(os.path.join(base, href)).replace("\\", "/")
        if p not in seen:
            seen.add(p)
            content.append(p)

    for idref in spine:
        if idref in manifest:
            add(manifest[idref])
    for href in manifest.values():
        if os.path.splitext(href)[1].lower() in XHTML_EXT:
            add(href)
    return content


def get_content_files(zf):
    opf_path = read_container(zf)
    content = read_opf(zf, opf_path)
    names = set(zf.namelist())
    return [c for c in content if c in names]


def is_image(name):
    low = name.lower()
    return os.path.splitext(low)[1] in IMAGE_EXT or "/images/" in low


def image_hashes(zf):
    out = {}
    for info in zf.infolist():
        if info.is_dir():
            continue
        if is_image(info.filename):
            out[info.filename] = hashlib.sha256(zf.read(info.filename)).hexdigest()
    return out


def write_epub(zf, translated, out_path):
    mimetype = b"application/epub+zip"
    with zipfile.ZipFile(out_path, "w") as zout:
        zout.writestr(zipfile.ZipInfo("mimetype"), mimetype,
                      compress_type=zipfile.ZIP_STORED)
        for info in zf.infolist():
            if info.filename == "mimetype":
                continue
            data = translated.get(info.filename)
            if data is None:
                data = zf.read(info.filename)
            zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            zi.external_attr = info.external_attr
            zi.compress_type = (
                zipfile.ZIP_DEFLATED
                if info.filename in translated
                else info.compress_type
            )
            zout.writestr(zi, data)


def validate_images(zf_orig, out_path):
    orig = image_hashes(zf_orig)
    with zipfile.ZipFile(out_path) as zout:
        new = image_hashes(zout)
    missing = sorted(set(orig) - set(new))
    changed = sorted(k for k in orig if k in new and orig[k] != new[k])
    if missing or changed:
        details = []
        if missing:
            details.append("imagens ausentes: " + ", ".join(missing))
        if changed:
            details.append("imagens modificadas: " + ", ".join(changed))
        raise RuntimeError("validacao de imagens falhou - " + "; ".join(details))
    return len(orig)
