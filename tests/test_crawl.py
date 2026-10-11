import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.fixture_site import Site
import crawl

def cfg(site):
    return {'user_agent': 'SCC-test', 'delay': 0, 'workers': 4, 'sites': [{
        'site': 't', 'base': site.base + '/', 'hosts': [f'127.0.0.1:{site.port}'], 'max_pages': 50,
        'host_checks': [{'url': f'http://localhost:{site.port}/', 'expect': site.base + '/'}],
        'redirect_checks': [{'from': '/legacy-old/', 'expect_to': '/about'}, {'from': '/legacy-ok/', 'expect_to': '/about'}, {'from': '/legacy-rebuilt/'}, {'from': '/legacy-hostfirst/'}]}]}

class CrawlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = Site(); cls.out = tempfile.mkdtemp()
        cls.res = crawl.run(cfg(cls.site), cls.out, today='2026-10-11')
    @classmethod
    def tearDownClass(cls): cls.site.stop()

    def issues(self, check): return [i for i in self.res['issues'] if i['check'] == check]
    def urls(self, check): return {crawl.path_of(i['url']) for i in self.issues(check)}

    def test_reports_ai_bot_blocked_in_robots(self):
        r = self.res['robots']['t']
        self.assertFalse(r['GPTBot']); self.assertTrue(r['ClaudeBot'])
        self.assertIn('GPTBot', self.issues('ai_bots_blocked')[0]['detail'])
    def test_flags_canonical_pointing_at_redirect(self):
        self.assertIn('/wiki/a', self.urls('canonical_redirects'))
    def test_flags_sitemap_url_returning_404(self):
        self.assertIn('/gone', self.urls('sitemap_non_200'))
    def test_flags_noindex_page_in_sitemap(self):
        self.assertIn('/tag/x', self.urls('sitemap_noindex'))
    def test_flags_broken_internal_link_with_source(self):
        i = [x for x in self.issues('broken_internal_link') if crawl.path_of(x['url']) == '/broken'][0]
        self.assertIn('/', i['detail'])
    def test_flags_internal_link_to_redirect(self):
        self.assertIn('/old-link', self.urls('internal_link_redirect'))
    def test_counts_images_missing_alt_and_size(self):
        self.assertIn('/', self.urls('img_missing_alt'))
        imgs = [i for i in self.res['images'] if crawl.path_of(i['page']) == '/']
        self.assertEqual(len(imgs), 2)
        self.assertEqual(sum(1 for i in imgs if i['alt'] is None), 1)
    def test_flags_duplicate_titles(self):
        self.assertEqual(self.urls('duplicate_title'), {'/', '/about'})
    def test_flags_h1_and_description_problems(self):
        self.assertIn('/about', self.urls('h1_count'))
        self.assertIn('/about', self.urls('missing_description'))
        self.assertNotIn('/', self.urls('missing_description'))
    def test_flags_orphan_sitemap_page(self):
        self.assertIn('/orphan', self.urls('orphan_page'))
        self.assertNotIn('/about', self.urls('orphan_page'))
    def test_checks_expected_redirects(self):
        c = {x['from']: x for x in self.res['checks'] if x['kind'] == 'redirect'}
        self.assertFalse(c['/legacy-old/']['ok']); self.assertTrue(c['/legacy-ok/']['ok'])
        self.assertIn('/legacy-old/', self.urls('redirect_missing'))
    def test_legacy_url_rebuilt_at_same_slug_passes(self):
        c = {x['from']: x for x in self.res['checks'] if x['kind'] == 'redirect'}
        self.assertTrue(c['/legacy-rebuilt/']['ok'])
    def test_redirect_that_ends_in_404_is_recorded_as_404(self):
        c = {x['from']: x for x in self.res['checks'] if x['kind'] == 'redirect'}
        self.assertFalse(c['/legacy-hostfirst/']['ok'])
        f = json.load(open(os.path.join(self.out, 'crawl_facts.json')))
        self.assertEqual(f['t']['/legacy-hostfirst']['status'], 404)
        self.assertEqual(f['t']['/legacy-ok']['status'], 301)
    def test_checks_host_canonicalisation(self):
        c = [x for x in self.res['checks'] if x['kind'] == 'host'][0]
        self.assertTrue(c['ok'])
    def test_records_internal_link_graph_with_anchor(self):
        self.assertTrue(any(crawl.path_of(l['from']) == '/' and crawl.path_of(l['to']) == '/about' and l['anchor'] == 'About us' for l in self.res['links']))
    def test_does_not_crawl_external_links(self):
        self.assertFalse(any('external.example' in p['url'] for p in self.res['pages']))
    def test_writes_crawl_facts_in_build2_format(self):
        f = json.load(open(os.path.join(self.out, 'crawl_facts.json')))
        self.assertEqual(set(f['t']['/about']), {'status', 'words', 'title_len', 'desc_len', 'h1'})
        self.assertEqual(f['t']['/about']['h1'], 2)
        self.assertEqual(f['t']['/gone']['status'], 404)
    def test_records_schema_types(self):
        p = [p for p in self.res['pages'] if crawl.path_of(p['url']) == '/'][0]
        self.assertIn('LocalBusiness', p['schema'])

class DiffTest(unittest.TestCase):
    def test_second_run_reports_changes_and_resolved_issues(self):
        site = Site(); out = tempfile.mkdtemp()
        try:
            crawl.run(cfg(site), out, today='2026-10-10')
            site.routes['/about'] = site.routes['/'][:2] + (site.routes['/about'][2].replace('Home — Test', 'About — Test').replace('<h1>Second H1</h1>', ''),)
            res = crawl.run(cfg(site), out, today='2026-10-11')
            ch = [c for c in res['changes'] if crawl.path_of(c['url']) == '/about' and c['field'] == 'title']
            self.assertEqual(ch[0]['before'], 'Home — Test'); self.assertEqual(ch[0]['after'], 'About — Test')
            resolved = [i for i in res['resolved'] if i['check'] == 'h1_count']
            self.assertEqual(len(resolved), 1)
            still = [i for i in res['issues'] if i['check'] == 'sitemap_non_200'][0]
            self.assertEqual(still['first_seen'], '2026-10-10')
        finally: site.stop()

if __name__ == '__main__': unittest.main()

class SingleSiteRunTest(unittest.TestCase):
    def test_crawling_one_site_keeps_other_sites_results(self):
        site = Site(); out = tempfile.mkdtemp()
        try:
            c = cfg(site); other = dict(c['sites'][0], site='u'); c['sites'].append(other)
            crawl.run(c, out, today='2026-10-10')
            only_t = dict(c, sites=[c['sites'][0]])
            crawl.run(only_t, out, today='2026-10-11')
            issues = json.load(open(os.path.join(out, 'crawl_issues.json')))
            self.assertTrue(any(i['site'] == 'u' for i in issues))
            facts = json.load(open(os.path.join(out, 'crawl_facts.json')))
            self.assertIn('u', facts)
        finally: site.stop()
