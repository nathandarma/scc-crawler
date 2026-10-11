import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import crawl

def issue(check, sev, url, first_seen, site='fo'):
    return dict(site=site, check=check, severity=sev, label=crawl.SEVERITY[check][1], url=url, detail='d', first_seen=first_seen)

class AlertsTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        issues = [issue('redirect_missing', 'critical', f'https://x/{i}/', '2026-09-01') for i in range(18)] + [
                  issue('missing_description', 'warning', 'https://x/new', '2026-10-09', 'nd'),
                  issue('missing_description', 'warning', 'https://x/old', '2026-08-01', 'nd'),
                  issue('thin_content', 'info', 'https://x/t', '2026-10-09', 'nd')]
        resolved = [dict(issue('canonical_redirects', 'warning', 'https://x/wiki/a', '2026-09-01', 'fv'), resolved='2026-10-08'),
                    dict(issue('long_title', 'info', 'https://x/l', '2026-09-01', 'nd'), resolved='2026-10-08')]
        for n, o in (('crawl_issues.json', issues), ('crawl_resolved.json', resolved)):
            with open(os.path.join(self.d, n), 'w') as fh: json.dump(o, fh)
        self.a = crawl.alerts_from_crawl(self.d, '2026-10-11')
    def test_groups_open_critical_issues_into_one_alert(self):
        a = [x for x in self.a if x['site'] == 'fo']
        self.assertEqual(len(a), 1); self.assertEqual(a[0]['severity'], 'critical'); self.assertIn('18', a[0]['title'])
    def test_warns_only_on_new_warnings(self):
        a = [x for x in self.a if x['site'] == 'nd' and x['severity'] == 'warning']
        self.assertEqual(len(a), 1); self.assertIn('/new', a[0]['detail']); self.assertNotIn('/old', a[0]['detail'])
    def test_ignores_info_issues(self):
        self.assertFalse(any('thin' in x['title'].lower() or 'over 70' in x['title'] for x in self.a))
    def test_reports_recent_fixes_as_wins(self):
        w = [x for x in self.a if x['severity'] == 'win']
        self.assertEqual(len(w), 1); self.assertEqual(w[0]['site'], 'fv')
    def test_missing_crawl_dir_gives_no_alerts(self):
        self.assertEqual(crawl.alerts_from_crawl('/nonexistent/', '2026-10-11'), [])

if __name__ == '__main__': unittest.main()
