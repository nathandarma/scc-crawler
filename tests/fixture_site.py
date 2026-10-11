"""A tiny local site that reproduces the issues found in the audit, for testing crawl.py."""
import http.server, threading, socketserver

def pages(base):
    P = {}
    def html(title=None, desc=None, h1s=(), body='', canonical=None, robots=None):
        h = '<html><head>'
        if title is not None: h += f'<title>{title}</title>'
        if desc: h += f'<meta name="description" content="{desc}">'
        if canonical: h += f'<link rel="canonical" href="{canonical}">'
        if robots: h += f'<meta name="robots" content="{robots}">'
        h += '<script type="application/ld+json">{"@context":"https://schema.org","@type":"LocalBusiness","name":"T"}</script>'
        h += '</head><body><nav><a href="/">Home</a></nav>'
        h += ''.join(f'<h1>{x}</h1>' for x in h1s) + body + '</body></html>'
        return (200, {'Content-Type': 'text/html; charset=utf-8'}, h)
    words = ' '.join(['photography'] * 400)
    P['/robots.txt'] = (200, {'Content-Type': 'text/plain'},
        f'User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n\nSitemap: {base}/sitemap.xml\n')
    P['/sitemap.xml'] = (200, {'Content-Type': 'application/xml'},
        f'<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>{base}/sitemap-pages.xml</loc></sitemap></sitemapindex>')
    P['/sitemap-pages.xml'] = (200, {'Content-Type': 'application/xml'},
        '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' +
        ''.join(f'<url><loc>{base}{u}</loc><lastmod>2026-10-01</lastmod></url>' for u in ['/', '/about', '/gone', '/tag/x', '/wiki/a', '/orphan']) + '</urlset>')
    P['/'] = html('Home — Test', 'A good description of the home page that is long enough to count.', ['Home'],
        f'<p>{words}</p><a href="/about">About us</a> <a href="/wiki/a">Wiki A</a> <a href="/broken">Broken</a> '
        '<a href="/old-link">Old</a> <a href="/tag/x">Tag</a> <a href="https://external.example/x">Ext</a>'
        '<img src="/img/one.jpg"><img src="/img/two.jpg" alt="Canberra headshot" width="800" height="600">')
    P['/about'] = html('Home — Test', None, ['About', 'Second H1'], '<p>short page</p><a href="/">Back home</a>')
    P['/wiki/a'] = html('Wiki A', 'Wiki page A description that is long enough to be a real description.', ['Wiki A'],
        f'<p>{words}</p>', canonical=f'{base}/wiki/a.html')
    P['/wiki/a.html'] = (301, {'Location': '/wiki/a'}, '')
    P['/old-link'] = (301, {'Location': '/about'}, '')
    P['/tag/x'] = html('Tag X', 'Tag page', ['Tag X'], '<p>tag</p>', robots='noindex,follow')
    P['/orphan'] = html('Orphan', 'An orphan page nobody links to but it is in the sitemap anyway.', ['Orphan'], f'<p>{words}</p>')
    P['/legacy-hostfirst/'] = (301, {'Location': '/gone'}, '')
    P['/legacy-ok/'] = (301, {'Location': '/about'}, '')
    P['/legacy-rebuilt/'] = html('Rebuilt', 'Rebuilt at the same slug, which is a fine outcome for a legacy URL.', ['Rebuilt'], '<p>x</p>')
    return P

class Site:
    """Serve the fixture on 127.0.0.1; requests with Host 'localhost' redirect to 127.0.0.1 (host canonicalisation)."""
    def __init__(self):
        site = self
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                host = self.headers.get('Host', '')
                if host.startswith('localhost'):
                    self.send_response(301); self.send_header('Location', site.base + self.path); self.end_headers(); return
                st, hd, body = site.routes.get(self.path.split('?')[0], (404, {'Content-Type': 'text/html'}, '<html><head><title>Not found</title></head><body>404</body></html>'))
                b = body.encode()
                self.send_response(st)
                for k, v in hd.items(): self.send_header(k, v)
                self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)
        socketserver.TCPServer.allow_reuse_address = True
        self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.port = self.httpd.server_address[1]
        self.base = f'http://127.0.0.1:{self.port}'
        self.routes = pages(self.base)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
    def stop(self): self.httpd.shutdown(); self.httpd.server_close()
