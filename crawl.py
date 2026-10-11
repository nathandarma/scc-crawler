#!/usr/bin/env python3
"""Search Command Centre — site crawler.

Usage:  python3 crawl.py crawl_config.json OUT_DIR [--today YYYY-MM-DD]

Crawls each site in the config (sitemap + internal links), runs SEO checks and writes:
  crawl_pages.json    one row per URL: status, title, description, canonical, H1s, words, schema, timing
  crawl_issues.json   open issues {site, check, severity, url, detail, first_seen}
  crawl_resolved.json issues present last run but gone now
  crawl_changes.json  field-level changes since last run (title, description, status, canonical, noindex, h1)
  crawl_links.json    internal link graph {site, from, to, anchor}   -> internal link suggester
  crawl_images.json   every <img> {site, page, src, alt, w, h}        -> image search tracker
  crawl_checks.json   host + legacy-redirect checks with pass/fail
  crawl_robots.json   which AI / search bots robots.txt allows, per site
  crawl_facts.json    {site: {path: {status, words, title_len, desc_len, h1}}} — drop-in for build2.py
Previous outputs in OUT_DIR are read first, so issues keep their first_seen date and changes are diffed.
Standard library only.
"""
import gzip, json, os, re, sys, time, datetime as dt
import urllib.request, urllib.error, urllib.parse, urllib.robotparser
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from concurrent.futures import ThreadPoolExecutor

BOTS = ['Googlebot', 'Bingbot', 'GPTBot', 'OAI-SearchBot', 'ChatGPT-User', 'ClaudeBot', 'Claude-SearchBot', 'Claude-User',
        'PerplexityBot', 'Google-Extended', 'Applebot-Extended', 'CCBot']
AI_SEARCH_BOTS = ['OAI-SearchBot', 'ChatGPT-User', 'Claude-SearchBot', 'Claude-User', 'PerplexityBot']
DEFAULT_EXCLUDE = [r'/cart\b', r'/account\b', r'/commerce/', r'[?&]format=', r'/s/', r'/config\b', r'/api/', r'/search\b',
                   r'\.(jpe?g|png|gif|webp|avif|svg|pdf|zip|mp4|mov|css|js|ico|xml|txt)(\?|$)']
SEVERITY = {  # check -> (severity, plain-English label)
    'broken_internal_link': ('critical', 'Internal link to a page that errors'),
    'sitemap_non_200': ('warning', 'Sitemap lists a URL that does not return 200'),
    'sitemap_noindex': ('warning', 'Sitemap lists a noindex page'),
    'canonical_redirects': ('warning', 'Canonical points at a redirecting URL'),
    'canonical_broken': ('critical', 'Canonical points at a broken URL'),
    'canonical_other': ('info', 'Canonical points at a different page'),
    'redirect_missing': ('critical', 'Legacy URL is not redirecting as planned'),
    'host_not_redirecting': ('warning', 'Alternate host does not redirect to the main site'),
    'ai_bots_blocked': ('warning', 'robots.txt blocks AI search crawlers'),
    'no_sitemap': ('warning', 'No XML sitemap found'),
    'missing_title': ('warning', 'Page has no title'),
    'duplicate_title': ('warning', 'Title shared with other pages'),
    'long_title': ('info', 'Title over 70 characters (truncated in results)'),
    'missing_description': ('warning', 'No meta description'),
    'duplicate_description': ('info', 'Meta description shared with other pages'),
    'long_description': ('info', 'Meta description over 160 characters'),
    'h1_count': ('warning', 'Page does not have exactly one H1'),
    'thin_content': ('info', 'Under 300 words'),
    'img_missing_alt': ('info', 'Images without alt text'),
    'img_missing_size': ('info', 'Images without width/height (layout shift risk)'),
    'internal_link_redirect': ('info', 'Internal link goes through a redirect'),
    'orphan_page': ('info', 'In the sitemap but no internal links point to it'),
    'slow_response': ('info', 'Server response over 1 second'),
}


# ---------------------------------------------------------------- fetching
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None
_OPENER = urllib.request.build_opener(_NoRedirect)

def fetch(url, ua, timeout=20, max_hops=10):
    """GET url, following redirects manually so the chain is recorded."""
    chain, cur = [], url
    for _ in range(max_hops):
        req = urllib.request.Request(cur, headers={'User-Agent': ua, 'Accept-Encoding': 'gzip',
                                                   'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'})
        t0 = time.time()
        try:
            r = _OPENER.open(req, timeout=timeout); status, headers, body = r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            status, headers = e.code, e.headers
            try: body = e.read()
            except Exception: body = b''
        except Exception as e:
            return dict(url=url, final_url=cur, status=None, error=str(e)[:200], chain=chain, ms=None, headers={}, body=b'')
        ms = int((time.time() - t0) * 1000)
        loc = headers.get('Location') if headers else None
        if status in (301, 302, 303, 307, 308) and loc:
            nxt = urllib.parse.urljoin(cur, loc); chain.append([status, cur, nxt]); cur = nxt; continue
        if headers and (headers.get('Content-Encoding') or '').lower() == 'gzip':
            try: body = gzip.decompress(body)
            except Exception: pass
        return dict(url=url, final_url=cur, status=status, chain=chain, ms=ms,
                    headers={k.lower(): v for k, v in (headers or {}).items()}, body=body)
    return dict(url=url, final_url=cur, status=None, error='redirect loop', chain=chain, ms=None, headers={}, body=b'')


# ---------------------------------------------------------------- parsing
class PageParser(HTMLParser):
    SKIP = {'script', 'style', 'noscript', 'template', 'svg'}
    def __init__(s):
        super().__init__(convert_charrefs=True)
        s.title = None; s._in_title = False; s.meta = {}; s.canonical = None; s.h1 = []; s._h1 = None
        s.links = []; s._a = None; s.imgs = []; s.text = []; s._skip = 0; s.jsonld = []; s._ld = None
    def handle_starttag(s, tag, attrs):
        a = {k.lower(): (v if v is not None else '') for k, v in attrs}
        if tag in s.SKIP:
            s._skip += 1
            if tag == 'script' and a.get('type', '').lower() == 'application/ld+json': s._ld = []
            return
        if s._skip: return
        if tag == 'title' and s.title is None: s._in_title = True; s.title = ''
        elif tag == 'meta':
            n = (a.get('name') or a.get('property') or '').lower()
            if n and n not in s.meta: s.meta[n] = a.get('content', '')
        elif tag == 'link' and 'canonical' in a.get('rel', '').lower().split() and s.canonical is None: s.canonical = a.get('href')
        elif tag == 'h1': s._h1 = []
        elif tag == 'a' and a.get('href'): s._a = [a['href'], [], a.get('rel', '')]
        elif tag == 'img':
            alt = dict(attrs).get('alt')  # None = attribute missing, '' = decorative
            s.imgs.append(dict(src=a.get('src') or a.get('data-src') or a.get('data-image') or '', alt=alt,
                               w=a.get('width') or None, h=a.get('height') or None))
    def handle_endtag(s, tag):
        if tag in s.SKIP:
            s._skip = max(0, s._skip - 1)
            if tag == 'script' and s._ld is not None: s.jsonld.append(''.join(s._ld)); s._ld = None
            return
        if s._skip: return
        if tag == 'title': s._in_title = False
        elif tag == 'h1' and s._h1 is not None: s.h1.append(' '.join(''.join(s._h1).split())); s._h1 = None
        elif tag == 'a' and s._a: s.links.append((s._a[0], ' '.join(''.join(s._a[1]).split()), s._a[2])); s._a = None
    def handle_data(s, d):
        if s._ld is not None: s._ld.append(d); return
        if s._skip: return
        if s._in_title: s.title += d; return
        s.text.append(d)
        if s._h1 is not None: s._h1.append(d)
        if s._a is not None: s._a[1].append(d)

def schema_types(blocks):
    out = set()
    def walk(o):
        if isinstance(o, dict):
            t = o.get('@type')
            if isinstance(t, str): out.add(t)
            elif isinstance(t, list): out.update(x for x in t if isinstance(x, str))
            for v in o.values(): walk(v)
        elif isinstance(o, list):
            for v in o: walk(v)
    for b in blocks:
        try: walk(json.loads(b))
        except Exception: pass
    return sorted(out)


# ---------------------------------------------------------------- url helpers
def path_of(u):
    p = urllib.parse.urlsplit(u)
    return (p.path or '/') + (('?' + p.query) if p.query else '')

def norm(u, base):
    u = urllib.parse.urljoin(base, u.strip())
    p = urllib.parse.urlsplit(u)
    if p.scheme not in ('http', 'https'): return None
    return urllib.parse.urlunsplit((p.scheme, p.netloc.lower(), p.path or '/', p.query, ''))

def fact_key(u):
    """Same key build2.py uses for crawl_facts: path without trailing slash, '/' for home."""
    return urllib.parse.urlsplit(u).path.rstrip('/') or '/'


# ---------------------------------------------------------------- sitemap + robots
def read_robots(base, ua):
    r = fetch(urllib.parse.urljoin(base, '/robots.txt'), ua)
    text = r['body'].decode('utf-8', 'replace') if r['status'] == 200 else ''
    rp = urllib.robotparser.RobotFileParser(); rp.parse(text.splitlines())
    home = urllib.parse.urljoin(base, '/')
    allowed = {b: rp.can_fetch(b, home) for b in BOTS}
    maps = re.findall(r'(?im)^\s*sitemap:\s*(\S+)', text)
    return allowed, maps

def read_sitemaps(urls, base, ua, limit=25):
    seen, found, queue = set(), {}, list(urls) or [urllib.parse.urljoin(base, p) for p in ('/sitemap.xml', '/sitemap-index.xml')]
    while queue and len(seen) < limit:
        u = queue.pop(0)
        if u in seen: continue
        seen.add(u)
        r = fetch(u, ua)
        if r['status'] != 200 or not r['body']: continue
        try: root = ET.fromstring(r['body'])
        except ET.ParseError: continue
        tag = root.tag.split('}')[-1]
        for el in root:
            loc = next((c.text.strip() for c in el if c.tag.split('}')[-1] == 'loc' and c.text), None)
            if not loc: continue
            if tag == 'sitemapindex': queue.append(loc)
            else: found[loc] = next((c.text for c in el if c.tag.split('}')[-1] == 'lastmod'), None)
    return found, sorted(seen)


# ---------------------------------------------------------------- crawl one site
def crawl_site(sc, ua, workers=4, delay=0.2):
    site, base = sc['site'], sc['base']
    hosts = {h.lower() for h in sc.get('hosts', [urllib.parse.urlsplit(base).netloc])}
    excl = [re.compile(x) for x in DEFAULT_EXCLUDE + sc.get('exclude', [])]
    def internal(u): return urllib.parse.urlsplit(u).netloc in hosts
    def wanted(u): return internal(u) and not any(x.search(u) for x in excl)

    robots, maps = read_robots(base, ua)
    smap, smap_files = read_sitemaps(maps, base, ua)
    smap = {norm(u, base): lm for u, lm in smap.items() if norm(u, base)}

    results, frontier, seen = {}, [norm(base, base)] + [u for u in smap if wanted(u)] + [norm(x, base) for x in sc.get('extra_urls', [])], set()
    links, images = [], []
    def visit(u):
        if delay: time.sleep(delay)
        r = fetch(u, ua); page = dict(site=site, url=u, final_url=r['final_url'], status=r['status'], error=r.get('error'),
                                      redirect=[c[0] for c in r['chain']], ms=r['ms'])
        ctype = r['headers'].get('content-type', '')
        if r['status'] == 200 and 'html' in ctype and not r['chain']:
            p = PageParser()
            try: p.feed(r['body'].decode('utf-8', 'replace'))
            except Exception: pass
            robots_meta = (p.meta.get('robots', '') + ',' + r['headers'].get('x-robots-tag', '')).lower()
            title = ' '.join((p.title or '').split()) or None
            desc = ' '.join(p.meta.get('description', '').split()) or None
            page.update(title=title, description=desc, canonical=norm(p.canonical, u) if p.canonical else None,
                        noindex='noindex' in robots_meta, h1=p.h1, words=len(re.findall(r'\w+', ' '.join(p.text))),
                        schema=schema_types(p.jsonld), kb=round(len(r['body']) / 1024))
            out = []
            for href, anchor, rel in p.links:
                t = norm(href, u)
                if t and internal(t): out.append(dict(site=site, **{'from': u}, to=t, anchor=anchor[:120], nofollow='nofollow' in rel))
            page['_out'] = out
            page['_imgs'] = [dict(site=site, page=u, src=norm(i['src'], u) if i['src'] else '', alt=i['alt'], w=i['w'], h=i['h']) for i in p.imgs]
        return page
    with ThreadPoolExecutor(workers) as pool:
        while frontier and len(results) < sc.get('max_pages', 500):
            batch = []
            for u in frontier:
                if u and u not in seen and len(seen) < sc.get('max_pages', 500): seen.add(u); batch.append(u)
            frontier = []
            for page in pool.map(visit, batch):
                results[page['url']] = page
                for l in page.pop('_out', []):
                    links.append(l)
                    if wanted(l['to']) and l['to'] not in seen: frontier.append(l['to'])
                images += page.pop('_imgs', [])

    # canonical targets that weren't crawled
    canon_status = {}
    for c in {p.get('canonical') for p in results.values() if p.get('canonical')} - {None}:
        if c in results: canon_status[c] = (results[c]['status'], results[c]['redirect'])
        else:
            r = fetch(c, ua); canon_status[c] = (r['status'], [x[0] for x in r['chain']])

    checks = []
    for hc in sc.get('host_checks', []):
        r = fetch(hc['url'], ua)
        ok = bool(r['chain']) and r['chain'][0][0] in (301, 308) and r['final_url'].rstrip('/') == hc['expect'].rstrip('/') and r['status'] == 200
        checks.append(dict(site=site, kind='host', **{'from': hc['url']}, expect=hc['expect'], got=r['final_url'],
                           first_status=r['chain'][0][0] if r['chain'] else r['status'], ok=ok))
    for rc in sc.get('redirect_checks', []):
        src = urllib.parse.urljoin(base, rc['from']); r = fetch(src, ua)
        exp = rc.get('expect_to')
        hit = (not exp) or path_of(r['final_url']).rstrip('/') == exp.rstrip('/')
        ok = (bool(r['chain']) and r['chain'][0][0] in (301, 308) and r['status'] == 200 and hit) or (not r['chain'] and r['status'] == 200)
        checks.append(dict(site=site, kind='redirect', **{'from': rc['from']}, expect=exp, got=path_of(r['final_url']) if r['chain'] else None,
                           first_status=r['chain'][0][0] if r['chain'] else r['status'], final_status=r['status'], ok=ok))
    return dict(pages=list(results.values()), links=links, images=images, robots=robots, sitemap=smap,
                sitemap_files=smap_files, canon_status=canon_status, checks=checks)


# ---------------------------------------------------------------- checks
def find_issues(site, base, c):
    I = []
    def add(check, url, detail): I.append(dict(site=site, check=check, severity=SEVERITY[check][0], label=SEVERITY[check][1], url=url, detail=detail))
    pages = {p['url']: p for p in c['pages']}
    ok = {u: p for u, p in pages.items() if p['status'] == 200 and not p['redirect'] and 'title' in p}
    indexable = {u: p for u, p in ok.items() if not p['noindex'] and (not p['canonical'] or p['canonical'] == u)}

    blocked = [b for b in AI_SEARCH_BOTS + ['GPTBot', 'ClaudeBot', 'Google-Extended'] if not c['robots'].get(b, True)]
    if blocked: add('ai_bots_blocked', base, 'Blocked: ' + ', '.join(blocked) + '. Search-oriented bots (OAI-SearchBot, Claude-SearchBot, PerplexityBot) are what AI answers cite from.')
    if not c['sitemap']: add('no_sitemap', base, 'Tried robots.txt Sitemap lines, /sitemap.xml and /sitemap-index.xml.')

    inlinks = {}
    for l in c['links']:
        if l['from'] != l['to']: inlinks.setdefault(l['to'], set()).add(l['from'])
    broken = {}
    for l in c['links']:
        t = pages.get(l['to'])
        if not t: continue
        if t['status'] is None or t['status'] >= 400: broken.setdefault(l['to'], set()).add(path_of(l['from']))
        elif t['redirect'] and l['to'] != base: broken.setdefault(('r', l['to']), set()).add(path_of(l['from']))
    for k, srcs in broken.items():
        if isinstance(k, tuple): add('internal_link_redirect', k[1], f'Linked from {len(srcs)} page(s), e.g. {", ".join(sorted(srcs)[:3])}. Point links straight at {path_of(pages[k[1]]["final_url"])}.')
        else: add('broken_internal_link', k, f'Returns {pages[k]["status"] or pages[k].get("error")}. Linked from {len(srcs)} page(s): {", ".join(sorted(srcs)[:5])}.')

    for u in c['sitemap']:
        p = pages.get(u)
        if not p: continue
        if p['status'] != 200 or p['redirect']:
            add('sitemap_non_200', u, f'Returns {"a redirect (" + ",".join(map(str, p["redirect"])) + ") to " + path_of(p["final_url"]) if p["redirect"] else p["status"]}.')
        elif p.get('noindex'): add('sitemap_noindex', u, 'Page says noindex but is listed in the sitemap.')
        elif u not in inlinks and u.rstrip('/') != base.rstrip('/'): add('orphan_page', u, 'Only reachable via the sitemap.')

    for u, p in ok.items():
        cn = p['canonical']
        if cn and cn != u:
            st, red = c['canon_status'].get(cn, (None, []))
            if red: add('canonical_redirects', u, f'Canonical {path_of(cn)} redirects ({",".join(map(str, red))}). Point it at the final URL.')
            elif st is None or st >= 400: add('canonical_broken', u, f'Canonical {path_of(cn)} returns {st}.')
            elif not p['noindex']: add('canonical_other', u, f'Canonical is {path_of(cn)}.')

    for field, chk in (('title', 'duplicate_title'), ('description', 'duplicate_description')):
        groups = {}
        for u, p in indexable.items():
            if p.get(field): groups.setdefault(p[field].lower(), []).append(u)
        for v, us in groups.items():
            if len(us) > 1:
                for u in us: add(chk, u, f'Same {field} on {len(us)} pages: {", ".join(path_of(x) for x in us[:4])}.')
    for u, p in indexable.items():
        if not p['title']: add('missing_title', u, '')
        elif len(p['title']) > 70: add('long_title', u, f'{len(p["title"])} characters.')
        if not p['description']: add('missing_description', u, 'Google will write its own snippet.')
        elif len(p['description']) > 160: add('long_description', u, f'{len(p["description"])} characters.')
        if len(p['h1']) != 1: add('h1_count', u, f'{len(p["h1"])} H1s' + (f': {", ".join(p["h1"][:3])}' if p['h1'] else '.'))
        if p['words'] < 300: add('thin_content', u, f'About {p["words"]} words including navigation.')
        if p['ms'] and p['ms'] > 1000: add('slow_response', u, f'{p["ms"]} ms.')
    per_page = {}
    for i in c['images']: per_page.setdefault(i['page'], []).append(i)
    for u, imgs in per_page.items():
        if u not in indexable: continue
        na = sum(1 for i in imgs if i['alt'] is None or re.fullmatch(r'(?i)(img|dsc|_?mg)?[_-]?\d+.*\.(jpe?g|png|webp)|.*\.(jpe?g|png|webp)', (i['alt'] or '').strip() or 'x.jpg'))
        ns = sum(1 for i in imgs if not (i['w'] and i['h']))
        if na: add('img_missing_alt', u, f'{na} of {len(imgs)} images have no alt text (or a file name as alt).')
        if ns: add('img_missing_size', u, f'{ns} of {len(imgs)} images have no width/height attributes.')
    for ch in c['checks']:
        if not ch['ok']:
            if ch['kind'] == 'redirect':
                add('redirect_missing', urllib.parse.urljoin(base, ch['from']), f'Ends in {ch["final_status"]}' + (f' → {ch["got"]}' if ch['got'] else '') + (f'; planned target {ch["expect"]}.' if ch['expect'] else '.'))
            else: add('host_not_redirecting', ch['from'], f'First response {ch["first_status"]}, ended at {ch["got"]}; expected a 301 to {ch["expect"]}.')
    return I


# ---------------------------------------------------------------- run + diff
WATCH = ('status', 'title', 'description', 'canonical', 'noindex', 'h1')

def _load(out, name, default):
    p = os.path.join(out, name)
    try:
        with open(p) as fh: return json.load(fh)
    except Exception: return default

def run(cfg, out, today=None):
    today = today or dt.date.today().isoformat()
    os.makedirs(out, exist_ok=True)
    prev_pages = {(p['site'], p['url']): p for p in _load(out, 'crawl_pages.json', [])}
    prev_issues = {(i['site'], i['check'], i['url']): i for i in _load(out, 'crawl_issues.json', [])}
    ua = cfg.get('user_agent', 'SearchCommandCentre-crawler/1.0')
    R = dict(pages=[], issues=[], links=[], images=[], checks=[], robots={}, changes=[], resolved=[], facts={}, summary=[])
    for sc in cfg['sites']:
        t0 = time.time()
        c = crawl_site(sc, ua, cfg.get('workers', 4), cfg.get('delay', 0.2))
        iss = find_issues(sc['site'], sc['base'], c)
        for i in iss:
            old = prev_issues.get((i['site'], i['check'], i['url']))
            i['first_seen'] = old['first_seen'] if old else today
        R['issues'] += iss; R['pages'] += c['pages']; R['links'] += c['links']; R['images'] += c['images']
        R['checks'] += c['checks']; R['robots'][sc['site']] = c['robots']
        f = R['facts'].setdefault(sc['site'], {})
        for p in c['pages']:
            if p['redirect'] and not (p['status'] and p['status'] >= 400): st = p['redirect'][0]
            else: st = p['status']
            f[fact_key(p['url'])] = dict(status=st, words=p.get('words', 0), title_len=len(p.get('title') or ''),
                                         desc_len=len(p.get('description') or ''), h1=len(p.get('h1') or []))
        for ch in c['checks']:
            if ch['kind'] == 'redirect':
                f[fact_key(ch['from'])] = dict(status=ch['first_status'] if ch['ok'] else ch['final_status'], words=0, title_len=0, desc_len=0, h1=0)
        for p in c['pages']:
            old = prev_pages.get((p['site'], p['url']))
            if not old: continue
            for k in WATCH:
                if old.get(k) != p.get(k) and not (k != 'status' and (old.get('status') != 200 or p.get('status') != 200)):
                    R['changes'].append(dict(site=p['site'], url=p['url'], field=k, before=old.get(k), after=p.get(k), date=today))
        R['summary'].append(dict(site=sc['site'], pages=len(c['pages']), sitemap_urls=len(c['sitemap']), issues=len(iss),
                                 critical=sum(1 for i in iss if i['severity'] == 'critical'), seconds=round(time.time() - t0, 1), date=today))
    now_keys = {(i['site'], i['check'], i['url']) for i in R['issues']}
    crawled_sites = {sc['site'] for sc in cfg['sites']}
    R['resolved'] = [dict(i, resolved=today) for k, i in prev_issues.items() if k not in now_keys and i['site'] in crawled_sites]
    # keep results for sites not crawled this run (e.g. --site sec)
    keep = lambda rows: [r for r in rows if r.get('site') not in crawled_sites]
    R['pages'] += keep(prev_pages.values()); R['issues'] += keep(prev_issues.values())
    for name in ('crawl_links', 'crawl_images', 'crawl_checks', 'crawl_summary'):
        R[name[6:]] += keep(_load(out, name + '.json', []))
    for name, key in (('crawl_robots.json', 'robots'), ('crawl_facts.json', 'facts')):
        for k, v in _load(out, name, {}).items():
            R[key].setdefault(k, v)
    for name, obj in (('crawl_pages', R['pages']), ('crawl_issues', R['issues']), ('crawl_resolved', R['resolved']), ('crawl_changes', R['changes']),
                      ('crawl_links', R['links']), ('crawl_images', R['images']), ('crawl_checks', R['checks']), ('crawl_robots', R['robots']),
                      ('crawl_facts', R['facts']), ('crawl_summary', R['summary'])):
        with open(os.path.join(out, name + '.json'), 'w') as fh: json.dump(obj, fh, separators=(',', ':'), ensure_ascii=False)
    return R


# ---------------------------------------------------------------- dashboard alerts (used by build2.py)
def alerts_from_crawl(crawl_dir, last_date, days=7):
    """Open critical issues (always), warnings first seen in the last `days`, and critical/warning fixes in the last `days`.
    One alert per site + check so a batch of broken redirects reads as a single item."""
    issues = _load(crawl_dir, 'crawl_issues.json', None)
    if issues is None: return []
    resolved = _load(crawl_dir, 'crawl_resolved.json', [])
    since = (dt.date.fromisoformat(last_date) - dt.timedelta(days=days)).isoformat()
    groups = {}
    for i in issues:
        if i['severity'] == 'critical' or (i['severity'] == 'warning' and i['first_seen'] >= since):
            groups.setdefault((i['site'], i['check'], i['severity'], 'open'), []).append(i)
    for i in resolved:
        if i['severity'] in ('critical', 'warning') and i.get('resolved', '') >= since:
            groups.setdefault((i['site'], i['check'], 'win', 'fixed'), []).append(i)
    out = []
    for (site, check, sev, state), rows in sorted(groups.items()):
        label = SEVERITY[check][1]
        eg = ', '.join(path_of(r['url']) for r in rows[:3]) + (f' and {len(rows) - 3} more' if len(rows) > 3 else '')
        if state == 'fixed':
            out.append(dict(site=site, date=last_date, severity='win', kind='Site health', title=f'Fixed: {label.lower()} ({len(rows)})', detail=f'No longer found on {eg}.'))
        else:
            first = rows[0]['detail'] if len(rows) == 1 else ''
            out.append(dict(site=site, date=last_date, severity=sev, kind='Site health', title=f'{label} ({len(rows)})' if len(rows) > 1 else label,
                            detail=(f'{eg}. {first}' if first else f'{eg}.').strip()))
    return out


if __name__ == '__main__':
    if len(sys.argv) < 3: sys.exit(__doc__)
    today = sys.argv[sys.argv.index('--today') + 1] if '--today' in sys.argv else None
    with open(sys.argv[1]) as fh: cfg = json.load(fh)
    only = sys.argv[sys.argv.index('--site') + 1].split(',') if '--site' in sys.argv else None
    if only: cfg['sites'] = [s for s in cfg['sites'] if s['site'] in only]
    res = run(cfg, sys.argv[2], today)
    for s in res['summary']:
        print(f"{s['site']:4s} {s['pages']:4d} pages  {s['sitemap_urls']:4d} in sitemap  {s['issues']:4d} issues ({s['critical']} critical)  {s['seconds']}s")
    print(f"changes {len(res['changes'])}  newly resolved {len(res['resolved'])}")
