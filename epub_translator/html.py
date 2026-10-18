import html as html_lib
import re

NON_TRANSLATABLE = {"pre", "code", "script", "style", "head", "title", "svg", "math"}

BLOCK = {
    "p", "div", "section", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "table", "thead", "tbody", "tfoot", "tr", "td",
    "th", "caption", "figure", "figcaption", "blockquote", "dl", "dt",
    "dd", "article", "aside", "nav", "header", "footer", "address",
    "main", "body", "html", "hr",
}

VOID = {
    "img", "br", "hr", "link", "meta", "input", "wbr", "col", "area",
    "base", "embed", "param", "source", "track",
}

_TAG_NAME_RE = re.compile(r"^</?([A-Za-z][A-Za-z0-9:_-]*)")
_PLACEHOLDER_RE = re.compile(r"\[\[(\d+)\]\]")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")
_WS_RE = re.compile(r"\s+")


def tokenize(html):
    i = 0
    n = len(html)
    while i < n:
        c = html[i]
        if c == "<":
            if html.startswith("<!--", i):
                j = html.find("-->", i)
                end = j + 3 if j != -1 else n
                yield ("comment", html[i:end])
                i = end
            elif html.startswith("<![CDATA[", i):
                j = html.find("]]>", i)
                end = j + 3 if j != -1 else n
                yield ("cdata", html[i:end])
                i = end
            elif html.startswith("<!", i):
                j = html.find(">", i)
                end = j + 1 if j != -1 else n
                yield ("decl", html[i:end])
                i = end
            elif html.startswith("<?", i):
                j = html.find("?>", i)
                end = j + 2 if j != -1 else n
                yield ("pi", html[i:end])
                i = end
            else:
                j = i + 1
                quote = None
                while j < n:
                    ch = html[j]
                    if quote:
                        if ch == quote:
                            quote = None
                    elif ch == '"' or ch == "'":
                        quote = ch
                    elif ch == ">":
                        break
                    j += 1
                end = j + 1 if j < n else n
                yield ("tag", html[i:end])
                i = end
        else:
            j = html.find("<", i)
            if j == -1:
                j = n
            yield ("text", html[i:j])
            i = j


def parse_tag(raw):
    closing = raw.startswith("</")
    m = _TAG_NAME_RE.match(raw)
    name = m.group(1).lower() if m else ""
    body = raw[:-1] if raw.endswith(">") else raw
    self_closing = body.rstrip().endswith("/") or (name in VOID and not closing)
    return name, closing, self_closing


def collapse_ws(s):
    return _WS_RE.sub(" ", s)


def escape_text(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def mask_inline(items):
    parts = []
    placeholders = []
    buf = []

    def flush_buf():
        if buf:
            idx = len(placeholders)
            placeholders.append("".join(buf))
            parts.append(f"[[{idx}]]")
            buf.clear()

    for kind, val in items:
        if kind == "text":
            flush_buf()
            parts.append(val)
        else:
            buf.append(val)
    flush_buf()
    return "".join(parts), placeholders


def unmask_raw(masked, placeholders):
    def repl(m):
        i = int(m.group(1))
        return placeholders[i] if i < len(placeholders) else m.group(0)

    return _PLACEHOLDER_RE.sub(repl, masked)


def unmask(masked, placeholders):
    return unmask_raw(escape_text(masked), placeholders)


def remap_placeholders(masked, placeholders):
    mapping = {}
    new_ph = []

    def repl(m):
        old = int(m.group(1))
        if old not in mapping:
            mapping[old] = len(new_ph)
            new_ph.append(placeholders[old] if old < len(placeholders) else m.group(0))
        return f"[[{mapping[old]}]]"

    return _PLACEHOLDER_RE.sub(repl, masked), new_ph


def extract_document(html):
    items = []
    segs = []
    inline = []
    code_stack = []

    def flush():
        nonlocal inline
        if not inline:
            return
        masked, ph = mask_inline(inline)
        inline = []
        text_part = _PLACEHOLDER_RE.sub("", masked)
        if text_part.strip():
            masked = masked.strip()
            if masked:
                segs.append({"pieces": [{"masked": masked, "placeholders": ph}]})
                items.append(("seg", len(segs) - 1))
        else:
            items.append(("raw", unmask_raw(masked, ph)))

    for kind, raw in tokenize(html):
        if kind == "text":
            if code_stack:
                items.append(("raw", raw))
                continue
            unesc = html_lib.unescape(raw)
            if unesc.strip() == "":
                flush()
                items.append(("raw", raw))
            else:
                inline.append(("text", collapse_ws(unesc)))
        elif kind == "tag":
            name, closing, self_closing = parse_tag(raw)
            if name in NON_TRANSLATABLE:
                flush()
                if self_closing or closing:
                    if closing and code_stack and code_stack[-1] == name:
                        code_stack.pop()
                else:
                    code_stack.append(name)
                items.append(("raw", raw))
            else:
                if code_stack:
                    items.append(("raw", raw))
                elif name in BLOCK:
                    flush()
                    items.append(("raw", raw))
                else:
                    inline.append(("tag", raw))
        else:
            flush()
            items.append(("raw", raw))

    flush()
    return {"items": items, "segs": segs}


def reconstruct(doc):
    parts = []
    for item in doc["items"]:
        if item[0] == "raw":
            parts.append(item[1])
        else:
            seg = doc["segs"][item[1]]
            parts.append("".join(p["fragment"] for p in seg["pieces"]))
    return "".join(parts)


def est_tokens(text):
    return max(1, len(text) // 4)


def split_into_sentences(text):
    parts = _SENTENCE_RE.split(text)
    return [p for p in parts if p.strip()]


def hard_split(text, max_chars):
    out = []
    while len(text) > max_chars:
        cut = text.rfind(" ", 0, max_chars)
        if cut < max_chars // 2:
            cut = max_chars
        out.append(text[:cut])
        text = text[cut:].lstrip()
    if text:
        out.append(text)
    return out


def split_piece(piece, max_tokens):
    max_chars = max_tokens * 4
    masked = piece["masked"]
    placeholders = piece["placeholders"]
    sentences = split_into_sentences(masked)
    if not sentences:
        sentences = [masked]

    groups = []
    cur = ""
    for s in sentences:
        if cur and len(cur) + len(s) > max_chars:
            groups.append(cur)
            cur = s
        else:
            cur = cur + s if cur else s
    if cur:
        groups.append(cur)

    result = []
    for g in groups:
        for sub in (hard_split(g, max_chars) if len(g) > max_chars else [g]):
            nm, np_ = remap_placeholders(sub, placeholders)
            result.append({"masked": nm, "placeholders": np_})
    return result


def prepare_pieces(segs, max_tokens):
    flat = []
    for seg in segs:
        seg["piece_idx"] = []
        for piece in seg["pieces"]:
            if est_tokens(piece["masked"]) > max_tokens:
                subs = split_piece(piece, max_tokens)
            else:
                subs = [piece]
            for sp in subs:
                seg["piece_idx"].append(len(flat))
                flat.append(sp)
    return flat


def chunk_pieces(flat, max_tokens):
    chunks = []
    cur = []
    cur_tok = 0
    for p in flat:
        t = est_tokens(p["masked"])
        if cur and cur_tok + t > max_tokens:
            chunks.append(cur)
            cur = []
            cur_tok = 0
        cur.append(p)
        cur_tok += t
    if cur:
        chunks.append(cur)
    return chunks
