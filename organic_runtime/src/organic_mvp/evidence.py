from __future__ import annotations

import gzip
import json
import logging
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from .config import AppConfig
from .db import MemoryDB
from .loggingx import AuditLog
from .util import html_to_text, human_slug, norm_space, safe_http_url, sha256_text, utcnow, content_words


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ''
    provider: str = ''
    rank: int = 0
    metadata: dict | None = None


@dataclass
class EvidenceDocument:
    source_id: str
    title: str
    url: str
    text: str
    provider: str
    trust: float
    cache_path: str
    metadata: dict


class HTTPClient:
    def __init__(self, config: AppConfig, logger: logging.Logger, audit: AuditLog):
        self.config = config
        self.logger = logger
        self.audit = audit
        self.ua = 'OrganicAI-MVP/0.1 (evidence-grounded autonomous learning prototype)'

    def _validate_dns(self, url: str) -> None:
        allow_private = bool(self.config.get('allow_private_web', False))
        ok, reason = safe_http_url(url, allow_private=allow_private)
        if not ok:
            raise ValueError(f'Blocked URL {url}: {reason}')
        if allow_private:
            return
        host = urllib.parse.urlsplit(url).hostname
        if not host:
            raise ValueError('URL has no host')
        try:
            import ipaddress
            for family, _socktype, _proto, _canon, sockaddr in socket.getaddrinfo(host, None):
                ip = ipaddress.ip_address(sockaddr[0])
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                    raise ValueError(f'Blocked DNS target {ip} for host {host}')
        except socket.gaierror:
            # Let urllib surface the network error. This preserves offline behavior.
            pass

    def get(self, url: str, headers: dict | None = None, timeout: int = 25, max_bytes: int | None = None) -> tuple[bytes, dict]:
        self._validate_dns(url)
        h = {'User-Agent': self.ua, 'Accept-Encoding': 'gzip'}
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, headers=h, method='GET')
        max_bytes = int(max_bytes or self.config.get('max_fetch_bytes', 2_500_000))
        self.audit.write('web_fetch_start', url=url)
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
            data = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError(f'Response exceeded max_fetch_bytes={max_bytes}')
            if resp.headers.get('Content-Encoding', '').lower() == 'gzip':
                data = gzip.decompress(data)
            meta = {
                'content_type': resp.headers.get('Content-Type', ''),
                'final_url': resp.geturl(),
                'status': getattr(resp, 'status', 200),
            }
        self.audit.write('web_fetch_complete', url=url, bytes=len(data), metadata=meta)
        return data, meta


class WikipediaProvider:
    def __init__(self, config: AppConfig, http: HTTPClient, logger: logging.Logger):
        self.config, self.http, self.logger = config, http, logger
        self.lang = str(config.get('wikipedia_language', 'en'))
        self.api = f'https://{self.lang}.wikipedia.org/w/api.php'

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        params = {
            'action': 'query', 'list': 'search', 'srsearch': query, 'srlimit': str(limit),
            'format': 'json', 'utf8': '1', 'origin': '*'
        }
        url = self.api + '?' + urllib.parse.urlencode(params)
        raw, _ = self.http.get(url, headers={'Accept': 'application/json'})
        payload = json.loads(raw.decode('utf-8', 'replace'))
        out = []
        for i, r in enumerate(payload.get('query', {}).get('search', []), 1):
            title = r.get('title', '')
            page_url = f'https://{self.lang}.wikipedia.org/wiki/' + urllib.parse.quote(title.replace(' ', '_'))
            snippet = re.sub('<[^>]+>', ' ', r.get('snippet', ''))
            out.append(SearchResult(title=title, url=page_url, snippet=norm_space(snippet), provider='wikipedia', rank=i,
                                    metadata={'pageid': r.get('pageid')}))
        return out

    def fetch(self, result: SearchResult) -> tuple[str, str, dict]:
        title = result.title
        params = {
            'action': 'query', 'prop': 'extracts|info', 'inprop': 'url', 'explaintext': '1', 'exsectionformat': 'plain',
            'redirects': '1', 'titles': title, 'format': 'json', 'utf8': '1', 'origin': '*'
        }
        url = self.api + '?' + urllib.parse.urlencode(params)
        raw, meta = self.http.get(url, headers={'Accept': 'application/json'}, max_bytes=int(self.config.get('max_fetch_bytes')))
        payload = json.loads(raw.decode('utf-8', 'replace'))
        pages = payload.get('query', {}).get('pages', {})
        if not pages:
            return title, '', meta
        page = next(iter(pages.values()))
        text = page.get('extract', '') or ''
        canonical = page.get('fullurl') or result.url
        meta.update({'canonical_url': canonical, 'pageid': page.get('pageid')})
        return page.get('title') or title, text, meta


class BraveProvider:
    def __init__(self, config: AppConfig, http: HTTPClient, logger: logging.Logger):
        self.config, self.http, self.logger = config, http, logger
        self.key = str(config.get('brave_search_api_key') or '')
        self.endpoint = 'https://api.search.brave.com/res/v1/web/search'

    def ready(self) -> bool:
        return bool(self.key)

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        if not self.key:
            return []
        url = self.endpoint + '?' + urllib.parse.urlencode({'q': query, 'count': min(limit, 20), 'safesearch': 'moderate'})
        raw, _ = self.http.get(url, headers={'Accept': 'application/json', 'X-Subscription-Token': self.key})
        payload = json.loads(raw.decode('utf-8', 'replace'))
        out = []
        for i, r in enumerate(payload.get('web', {}).get('results', []), 1):
            out.append(SearchResult(title=r.get('title', ''), url=r.get('url', ''), snippet=norm_space(r.get('description', '')),
                                    provider='brave', rank=i, metadata={'age': r.get('age'), 'profile': r.get('profile', {})}))
        return out




class LocalCorpusProvider:
    """Search raw local text files as an offline stand-in for web search.

    This provider is useful for controlled developmental tests with real public-domain
    textbooks. The input remains raw unstructured prose; it is not pre-encoded graph data.
    """
    def __init__(self, config: AppConfig, logger: logging.Logger):
        self.config, self.logger = config, logger

    def root(self) -> Path | None:
        raw = str(self.config.get('local_corpus_dir') or '').strip()
        if not raw:
            return None
        p = Path(raw).expanduser()
        return p if p.exists() and p.is_dir() else None

    def ready(self) -> bool:
        return self.root() is not None

    def _read(self, path: Path) -> tuple[dict, str]:
        raw = path.read_text(encoding='utf-8', errors='replace')
        head, sep, body = raw.partition('\n\n')
        meta = {}
        for line in head.splitlines():
            if ':' in line:
                k, v = line.split(':', 1)
                meta[k.strip().lower()] = v.strip()
        if not sep:
            body = raw
        return meta, norm_space(body)

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        root = self.root()
        if root is None:
            return []
        q = set(content_words(query))
        ranked = []
        for path in root.rglob('*.txt'):
            if not path.is_file():
                continue
            try:
                meta, body = self._read(path)
            except Exception:
                continue
            toks = set(content_words((meta.get('source_title') or meta.get('title') or path.stem) + ' ' + body[:60000]))
            if not q:
                score = 0.0
            else:
                score = len(q & toks) / max(1, len(q))
                # Reward exact phrase occurrence and repeated term presence.
                low = body.lower()
                nq = norm_space(query).lower()
                if nq and nq in low:
                    score += 0.4
                score += min(0.25, sum(min(low.count(t), 4) for t in q) * 0.015)
            if score <= 0:
                continue
            title = meta.get('source_title') or meta.get('title') or path.stem
            url = meta.get('source_url') or meta.get('url') or ('file://' + str(path.resolve()))
            snippet = body[:700]
            ranked.append((score, SearchResult(title=title, url=url, snippet=snippet, provider='local_corpus', rank=0,
                                               metadata={'path': str(path), 'score': score, 'license_status': meta.get('license_status', '')})))
        ranked.sort(key=lambda x: x[0], reverse=True)
        out = []
        for i, (_score, r) in enumerate(ranked[:limit], 1):
            r.rank = i
            out.append(r)
        return out

    def fetch(self, result: SearchResult) -> tuple[str, str, dict]:
        path = Path((result.metadata or {}).get('path', ''))
        if not path.exists():
            raise FileNotFoundError(path)
        meta, body = self._read(path)
        title = meta.get('source_title') or meta.get('title') or result.title
        md = dict(result.metadata or {})
        md.update(meta)
        md['local_path'] = str(path)
        return title, body, md


class DirectFetcher:
    def __init__(self, config: AppConfig, http: HTTPClient, logger: logging.Logger):
        self.config, self.http, self.logger = config, http, logger

    def fetch(self, url: str) -> tuple[str, str, dict]:
        raw, meta = self.http.get(url, headers={'Accept': 'text/html,text/plain,application/xhtml+xml;q=0.9,*/*;q=0.2'})
        ctype = (meta.get('content_type') or '').lower()
        charset = 'utf-8'
        m = re.search(r'charset=([\w\-]+)', ctype)
        if m:
            charset = m.group(1)
        text_raw = raw.decode(charset, 'replace')
        if 'html' in ctype or '<html' in text_raw[:500].lower():
            title, text = html_to_text(text_raw)
        else:
            title, text = '', text_raw
        return title or urllib.parse.urlsplit(meta.get('final_url') or url).hostname or url, text, meta


class EvidenceBroker:
    def __init__(self, root: Path, config: AppConfig, db: MemoryDB, logger: logging.Logger, audit: AuditLog):
        self.root = root
        self.config = config
        self.db = db
        self.logger = logger
        self.audit = audit
        self.cache_dir = root / 'data' / 'source_cache'
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.http = HTTPClient(config, logger, audit)
        self.wikipedia = WikipediaProvider(config, self.http, logger)
        self.brave = BraveProvider(config, self.http, logger)
        self.local_corpus = LocalCorpusProvider(config, logger)
        self.direct = DirectFetcher(config, self.http, logger)

    def provider_status(self) -> dict:
        mode = str(self.config.get('web_provider', 'auto'))
        return {
            'mode': mode,
            'brave_configured': self.brave.ready(),
            'wikipedia_available_without_key': True,
            'local_corpus_ready': self.local_corpus.ready(),
            'direct_url_fetch': True,
            'private_web_allowed': bool(self.config.get('allow_private_web', False)),
        }

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        query = norm_space(query)
        if not query:
            return []
        mode = str(self.config.get('web_provider', 'auto')).lower()
        self.audit.write('web_search', query=query, provider_mode=mode)
        errors = []
        if mode == 'local_corpus':
            results = self.local_corpus.search(query, limit=limit)
            self.audit.write('web_search_results', query=query, provider='local_corpus', count=len(results))
            return results
        if mode in {'auto', 'brave'} and self.brave.ready():
            try:
                results = self.brave.search(query, limit=limit)
                if results:
                    self.audit.write('web_search_results', query=query, provider='brave', count=len(results))
                    return results
            except Exception as exc:
                errors.append(f'brave: {exc}')
                self.logger.warning('Brave search failed: %s', exc)
                if mode == 'brave':
                    raise
        if mode in {'auto', 'wikipedia', 'brave'}:
            try:
                results = self.wikipedia.search(query, limit=limit)
                self.audit.write('web_search_results', query=query, provider='wikipedia', count=len(results))
                return results
            except Exception as exc:
                errors.append(f'wikipedia: {exc}')
                self.logger.warning('Wikipedia search failed: %s', exc)
        if errors:
            self.audit.write('web_search_failed', query=query, errors=errors)
        return []

    def _cache_document(self, title: str, url: str, provider: str, text: str, trust: float, metadata: dict) -> EvidenceDocument:
        text = text[:int(self.config.get('max_source_chars', 180_000))]
        digest = sha256_text(text)
        source_id = f'source:{digest[:20]}'
        safe = human_slug(title or provider)
        path = self.cache_dir / f'{safe}_{digest[:12]}.txt'
        header = {
            'title': title, 'url': url, 'provider': provider, 'retrieved_at': utcnow(), 'sha256': digest,
            'trust': trust, 'metadata': metadata,
        }
        if not path.exists():
            path.write_text('ORGANIC_SOURCE_METADATA=' + json.dumps(header, ensure_ascii=False) + '\n\n' + text, encoding='utf-8')
        self.db.add_source(source_id, url, title, provider, digest, str(path.relative_to(self.root)), trust, metadata)
        self.audit.write('source_cached', source_id=source_id, title=title, url=url, provider=provider, chars=len(text), sha256=digest)
        return EvidenceDocument(source_id, title, url, text, provider, trust, str(path.relative_to(self.root)), metadata)

    def fetch_result(self, result: SearchResult) -> EvidenceDocument:
        cached = self.db.source_by_url(result.url)
        if cached and cached['cache_path']:
            p = self.root / cached['cache_path']
            if p.exists():
                raw = p.read_text(encoding='utf-8', errors='replace')
                _head, _sep, text = raw.partition('\n\n')
                return EvidenceDocument(cached['source_id'], cached['title'] or result.title, cached['url'], text,
                                        cached['provider'], float(cached['trust']), cached['cache_path'], {})
        if result.provider == 'local_corpus':
            title, text, meta = self.local_corpus.fetch(result)
            return self._cache_document(title, result.url, 'local_corpus', text, 0.78, meta)
        if result.provider == 'wikipedia':
            title, text, meta = self.wikipedia.fetch(result)
            url = meta.get('canonical_url') or result.url
            return self._cache_document(title, url, 'wikipedia', text, 0.72, meta)
        title, text, meta = self.direct.fetch(result.url)
        return self._cache_document(title or result.title, meta.get('final_url') or result.url, result.provider or 'web', text, 0.58, meta)

    def fetch_url(self, url: str) -> EvidenceDocument:
        cached = self.db.source_by_url(url)
        if cached and cached['cache_path']:
            p = self.root / cached['cache_path']
            if p.exists():
                raw = p.read_text(encoding='utf-8', errors='replace')
                _head, _sep, text = raw.partition('\n\n')
                return EvidenceDocument(cached['source_id'], cached['title'] or url, cached['url'], text,
                                        cached['provider'], float(cached['trust']), cached['cache_path'], {})
        title, text, meta = self.direct.fetch(url)
        return self._cache_document(title, meta.get('final_url') or url, 'direct', text, 0.55, meta)
