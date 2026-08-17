from __future__ import annotations

import datetime as dt
import hashlib
import html
import ipaddress
import json
import os
import re
import socket
import urllib.parse
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, List, Optional

STOPWORDS = {
    'a','about','above','after','again','against','all','also','am','an','and','any','are','as','at','be','because','been',
    'before','being','below','between','both','but','by','can','could','did','do','does','doing','down','during','each','few',
    'for','from','further','had','has','have','having','he','her','here','hers','herself','him','himself','his','how','i','if',
    'in','into','is','it','its','itself','just','me','more','most','my','myself','no','nor','not','now','of','off','on','once',
    'only','or','other','our','ours','ourselves','out','over','own','same','she','should','so','some','such','than','that','the',
    'their','theirs','them','themselves','then','there','these','they','this','those','through','to','too','under','until','up',
    'very','was','we','were','what','when','where','which','while','who','whom','why','will','with','would','you','your','yours',
    'yourself','yourselves','explain','describe','tell','please','learn','understand','information','thing','things'
}

GENERIC_RELATION_VERBS = {
    'is','are','was','were','be','become','becomes','became','has','have','had','contains','contain','include','includes',
    'use','uses','used','make','makes','made','produce','produces','produced','release','releases','released','allow','allows',
    'regulate','regulates','regulated','open','opens','opened','close','closes','closed','absorb','absorbs','absorbed','pass',
    'passes','escape','escapes','take','takes','reach','reaches','lose','loses','form','forms','derive','derives','represent',
    'represents','increase','increases','decrease','decreases','cause','causes','draw','draws','carry','carries','enter','enters',
    'diffuse','diffuses','convert','converts','split','splits','build','builds','supply','supplies','require','requires','consist',
    'consists','depend','depends','prevent','prevents','connect','connects','occur','occurs','provide','provides','help','helps',
}


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8', 'ignore')).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def compact_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def norm_space(text: str) -> str:
    return re.sub(r'\s+', ' ', text or '').strip()


def normalize_label(text: str) -> str:
    text = html.unescape(text or '').lower()
    text = re.sub(r'[^a-z0-9\s\-\']+', ' ', text)
    return norm_space(text)


def words(text: str) -> List[str]:
    return re.findall(r"[A-Za-z][A-Za-z0-9'\-]{1,}", (text or '').lower())


def content_words(text: str) -> List[str]:
    return [w for w in words(text) if len(w) >= 3 and w not in STOPWORDS]


def sentence_split(text: str) -> List[str]:
    text = norm_space(text)
    if not text:
        return []
    # Conservative sentence segmentation without external libraries.
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z0-9\"\'])', text)
    return [p.strip() for p in parts if len(p.strip()) >= 20]


def stable_uid(prefix: str, text: str) -> str:
    digest = hashlib.sha256(normalize_label(text).encode('utf-8')).hexdigest()[:16]
    return f'{prefix}:{digest}'


def human_slug(text: str, max_len: int = 48) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', normalize_label(text)).strip('-')
    return (slug[:max_len] or 'item').rstrip('-')


def overlap_score(a: str, b: str) -> float:
    sa, sb = set(content_words(a)), set(content_words(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(1, len(sa | sb))


def contains_negation(text: str) -> bool:
    t = f' {normalize_label(text)} '
    return any(x in t for x in (' not ', ' never ', ' no ', ' cannot ', " can't ", ' does not ', ' do not ', ' did not '))


class PlainTextHTMLParser(HTMLParser):
    SKIP = {'script', 'style', 'noscript', 'svg', 'canvas', 'iframe'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._chunks: list[str] = []
        self.title: str = ''
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self.SKIP:
            self._skip_depth += 1
        if tag == 'title':
            self._in_title = True
        if tag in {'p','div','section','article','li','h1','h2','h3','h4','h5','h6','br','tr'}:
            self._chunks.append('\n')

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
        if tag == 'title':
            self._in_title = False
        if tag in {'p','div','section','article','li','h1','h2','h3','h4','h5','h6','tr'}:
            self._chunks.append('\n')

    def handle_data(self, data):
        if self._skip_depth:
            return
        d = norm_space(data)
        if not d:
            return
        if self._in_title and not self.title:
            self.title = d
        self._chunks.append(d + ' ')

    def text(self) -> str:
        raw = ''.join(self._chunks)
        lines = [norm_space(x) for x in raw.splitlines()]
        return '\n'.join(x for x in lines if x)


def html_to_text(raw_html: str) -> tuple[str, str]:
    p = PlainTextHTMLParser()
    p.feed(raw_html)
    return p.title, p.text()


def safe_http_url(url: str, allow_private: bool = False) -> tuple[bool, str]:
    """Best-effort SSRF protection for the web broker."""
    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception as exc:
        return False, f'invalid URL: {exc}'
    if parsed.scheme not in {'http', 'https'}:
        return False, 'only http/https URLs are allowed'
    if not parsed.hostname:
        return False, 'URL has no hostname'
    host = parsed.hostname.lower().strip('.')
    if host in {'localhost', 'localhost.localdomain'}:
        return (allow_private, 'localhost is private')
    try:
        ip = ipaddress.ip_address(host)
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast) and not allow_private:
            return False, 'private/link-local/reserved IP blocked'
    except ValueError:
        pass
    # DNS resolution is intentionally not required here. Runtime fetch performs a
    # second check on the connected host where possible.
    return True, 'ok'


def redact_dict(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        lk = k.lower()
        if any(x in lk for x in ('key', 'token', 'secret', 'password')):
            out[k] = '***REDACTED***' if v else ''
        elif isinstance(v, dict):
            out[k] = redact_dict(v)
        else:
            out[k] = v
    return out


@dataclass
class RankedText:
    text: str
    score: float
    source_id: Optional[str] = None
    claim_id: Optional[str] = None
