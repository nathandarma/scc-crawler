"""Daily-grain dataset builder for Search Command Centre.
Inputs (in DATA dir): raw GSC/GA API JSON dumps. Outputs (OUT dir): dashboard datasets.
Sparse series encoding: day index d = days since BASE (2025-06-01).
"""
import json, collections as C, datetime as dt, re, os, sys, glob, statistics as st
DATA = sys.argv[1] if len(sys.argv) > 1 else '/home/claude/data2/'
STATIC = DATA + 'static/'
OUT = sys.argv[2] if len(sys.argv) > 2 else '/home/claude/out2/'
os.makedirs(OUT, exist_ok=True)
BASE = dt.date(2025, 6, 1)
def di(s): return (dt.date.fromisoformat(s) - BASE).days
def J(p): return json.load(open(p))
def ga_rows(p):
    d = J(p); dh = [h['name'] for h in d['dimension_headers']]; mh = [h['name'] for h in d['metric_headers']]
    for r in d['rows']:
        yield dict(zip(dh, [v['value'] for v in r['dimension_values']])) | {m: float(v['value']) for m, v in zip(mh, r['metric_values'])}
def gdate(s): return f'{s[:4]}-{s[4:6]}-{s[6:]}'
def norm_page(p): return re.sub(r'^https?://(www\.)?[^/]+', '', p) or '/'
def dump(name, obj):
    json.dump(obj, open(OUT + name + '.json', 'w'), separators=(',', ':'), ensure_ascii=False)
    print(f'{name:14s} {len(obj) if isinstance(obj, list) else "-":>7} rows {os.path.getsize(OUT + name + ".json")/1024:8.0f} KB')

CLUSTERS = {
 'nd': [('X100VI', r'x100|xv100|x 100'), ('X-Half', r'x ?half|xhalf'), ('Sony A7CII', r'a7c|a7 c'), ('Lightroom presets', r'preset|lightroom'),
        ('Studios & photographers (local)', r'studio|photograph(er|y) (in |near )?canberra|canberra photo|near me'), ('Bags & cases', r'wandrd|nanuk|bag|sling|case|prvke'),
        ('Desk & accessories', r'benq|screenbar|halo|ecoflow|power ?bank|glove|otex|wagon|tripod|fotopro'), ('Lenses', r'samyang|lens|24mm'), ('Instax', r'instax'),
        ('Travel', r'ghibli|japan|bali|pier one|pet friendly|villa'), ('Services', r'boudoir|lingerie|wedding|headshot|event|real estate|car photo|automotive|branding|brand photo|product photo|swimwear'),
        ('Beginner cameras', r'beginner|best camera'), ('Brand', r'darma|nathan')],
 'fv': [('Brand', r'fovea'), ('Aftershoot', r'aftershoot|after shoot'), ('Narrative Select', r'narrative'), ('Photo Mechanic', r'photo ?mechanic'),
        ('Pixieset / Pic-Time', r'pixieset|pic-?time|pictime|sunflare|gallery'), ('Lightroom', r'lightroom|adobe'), ('Culling software', r'cull'), ('Capture One', r'capture one')],
 'fo': [('Brand', r'forte'), ('Renovations', r'renovat|kitchen|bathroom|laundry|remodel'), ('Extensions', r'extension'),
        ('Custom & new homes', r'custom|new home|luxury|bespoke|house (builder|construction)|home builder|builder'), ('Design', r'design')],
}
def cluster(site, q):
    for n, rx in CLUSTERS.get(site, []):
        if re.search(rx, q): return n
    return 'Other'
def intent(q):
    for n, rx in [('Transactional', r'\b(buy|price|pricing|cost|hire|book|near me|quote|discount|deal)\b'), ('Commercial', r'\b(best|vs|alternative|alternatives|review|reviews|compare|top)\b'), ('Navigational', r'fovea|darma|forte|nathan')]:
        if re.search(rx, q): return n
    return 'Informational'

# ---------------- raw loading ----------------
nd_q = []
for f in sorted(glob.glob(DATA + 'nd_qd_*.json')): nd_q += J(f)['rows']
fv_all = J(DATA + 'fv_all.json')['rows']; fo_all = J(DATA + 'fo_all.json')['rows']   # keys: date, query, page, device, country

# ---------------- gsc_daily ----------------
gsc_daily = [dict(site='nd', date=r['keys'][0], clicks=r['clicks'], impressions=r['impressions'], position=round(r['position'], 2)) for r in J(DATA + 'nd_date.json')['rows']]
def agg_by(rows, keyf):
    a = C.defaultdict(lambda: [0, 0, 0.0])
    for r in rows:
        k = keyf(r); x = a[k]; x[0] += r['clicks']; x[1] += r['impressions']; x[2] += r['position'] * r['impressions']
    return a
for site in ('fv', 'fo'):
    rows = J(DATA + site + '_dcd.json')['rows']   # date × country × device: sums to property totals
    for d, (c, i, pw) in sorted(agg_by(rows, lambda r: r['keys'][0]).items()):
        gsc_daily.append(dict(site=site, date=d, clicks=c, impressions=i, position=round(pw / i, 2) if i else None))
dump('gsc_daily', gsc_daily)
LAST = max(r['date'] for r in gsc_daily)

# ---------------- top page per query (latest 90 days of query×page) ----------------
def top_pages_from(rows, qi, pi):
    m = C.defaultdict(C.Counter)
    for r in rows: m[r['keys'][qi].strip().lower()][norm_page(r['keys'][pi])] += r['impressions']
    return m
tp = {'nd': top_pages_from(J(DATA + 'nd_qp.json')['rows'], 0, 1),
      'fv': top_pages_from(fv_all, 1, 2), 'fo': top_pages_from(fo_all, 1, 2)}

# ---------------- query series (sparse) ----------------
def series(rows, keyf, min_impr=0):
    s = C.defaultdict(dict)
    for r in rows:
        k = keyf(r); d = di(r['keys'][0]); x = s[k].setdefault(d, [0, 0, 0.0])
        x[0] += r['clicks']; x[1] += r['impressions']; x[2] += r['position'] * r['impressions']
    out = []
    for k, days in s.items():
        if sum(v[1] for v in days.values()) < min_impr: continue
        ds = sorted(days)
        out.append((k, ds, [days[d][0] for d in ds], [days[d][1] for d in ds], [round(days[d][2] / days[d][1], 1) if days[d][1] else 0 for d in ds]))
    return out
for site, rows, mn in (('nd', nd_q, 20), ('fv', [dict(keys=[r['keys'][0], r['keys'][1].strip().lower()], **{k: r[k] for k in ('clicks', 'impressions', 'position')}) for r in fv_all], 0),
                       ('fo', [dict(keys=[r['keys'][0], r['keys'][1].strip().lower()], **{k: r[k] for k in ('clicks', 'impressions', 'position')}) for r in fo_all], 0)):
    ser = series(rows, lambda r: r['keys'][1].strip().lower(), mn)
    obj = []
    for q, ds, c, i, p in sorted(ser, key=lambda x: -sum(x[3])):
        pages = tp[site].get(q, C.Counter())
        top = pages.most_common(1)[0][0] if pages else ''
        npg = sum(1 for pg, v in pages.items() if v >= max(5, .1 * sum(pages.values())))
        obj.append(dict(query=q, cluster=cluster(site, q), intent=intent(q), top_page=top, ranking_pages=npg, d=ds, c=c, i=i, p=p))
    dump('q_' + site, obj)

# ---------------- page series + crawl facts ----------------
CRAWL = J(STATIC + 'crawl_facts.json')
LIVE = DATA + 'crawl/'   # live crawler output (crawl.py); overrides the static snapshot page by page
if os.path.exists(LIVE + 'crawl_facts.json'):
    for _s, _f in J(LIVE + 'crawl_facts.json').items(): CRAWL.setdefault(_s, {}).update(_f)
def fo_target(p):
    if 'about' in p: return '/about'
    if re.search(r'kitchen|bathroom|laundry', p) and 'home-bathroom-kitchen' not in p: return '/kitchen-bathroom-renovations-canberra (new) · interim /our-projects'
    if 'renovation' in p: return '/renovations-canberra (new) · interim /our-projects'
    if 'extension' in p: return '/house-extensions-canberra (new) · interim /our-projects'
    return '/custom-homes-canberra (new) · interim /'
nd_pd = J(DATA + 'nd_pd.json')['rows']
for site, rows, keyf in (('nd', nd_pd, lambda r: norm_page(r['keys'][1])),
                         ('fv', fv_all, lambda r: norm_page(r['keys'][2]).rstrip('/') or '/'),
                         ('fo', fo_all, lambda r: r['keys'][2])):
    ser = series(rows, keyf)
    obj = []
    for pg, ds, c, i, p in sorted(ser, key=lambda x: -sum(x[3])):
        cf = CRAWL.get(site, {}).get(norm_page(pg).rstrip('/') or '/', {})
        obj.append(dict(page=pg, status=cf.get('status'), words=cf.get('words'), title_len=cf.get('title_len'), desc_len=cf.get('desc_len'), h1=cf.get('h1'),
                        redirect_to=fo_target(pg) if site == 'fo' and cf.get('status') == 404 else None, d=ds, c=c, i=i, p=p))
    dump('p_' + site, obj)

# ---------------- GA landing pages daily ----------------
lp = C.defaultdict(lambda: [0, 0, 0, 0])
for site, f in (('nd', 'ga_nd_lpd.json'), ('fv', 'ga_fv_lpd.json')):
    for r in ga_rows(DATA + f):
        page = (r['landingPage'].split('?')[0] or '/')
        org = 1 if r['sessionDefaultChannelGroup'] == 'Organic Search' else 0
        x = lp[(site, gdate(r['date']), page, org)]
        x[0] += r['sessions']; x[1] += r['engagedSessions']; x[2] += r['keyEvents']; x[3] += r['userEngagementDuration']
lp_daily = [dict(site=s, date=d, page=p, organic=o, sessions=int(v[0]), engaged=int(v[1]), key_events=int(v[2]), eng_seconds=int(v[3])) for (s, d, p, o), v in sorted(lp.items())]
dump('lp_daily', lp_daily)

# ---------------- GA daily by channel ----------------
ga_daily = []
CH = {'Organic Search': 'organic', 'Direct': 'direct', 'Referral': 'referral', 'Organic Social': 'social', 'AI Assistant': 'ai'}
for site, f in (('nd', 'ga_nd_daily.json'), ('fv', 'ga_fv_daily.json'), ('sec', 'ga_sec_daily.json')):
    if not os.path.exists(DATA + f): continue
    a = C.defaultdict(C.Counter)
    for r in ga_rows(DATA + f):
        d = gdate(r['date']); k = CH.get(r['sessionDefaultChannelGroup'], 'other')
        a[d]['sessions'] += r['sessions']; a[d][k] += r['sessions']; a[d]['engaged'] += r.get('engagedSessions', 0); a[d]['key_events'] += r.get('keyEvents', 0)
    for d in sorted(a):
        v = a[d]; ga_daily.append(dict(site=site, date=d, **{k: int(v[k]) for k in ('sessions', 'organic', 'direct', 'referral', 'social', 'ai', 'other', 'engaged', 'key_events')}))
dump('ga_daily', ga_daily)

# ---------------- segments daily ----------------
seg = []
for r in J(DATA + 'nd_dev.json')['rows']:
    seg.append(dict(site='nd', date=r['keys'][0], dim='Device', value=r['keys'][1].title(), clicks=r['clicks'], impressions=r['impressions'], pos_w=round(r['position'] * r['impressions'], 1)))
cty = J(DATA + 'nd_cty.json')['rows']; topc = [k for k, _ in C.Counter({r['keys'][1]: 0 for r in cty}).items()]
tot = C.Counter();
for r in cty: tot[r['keys'][1]] += r['impressions']
keep = {k for k, _ in tot.most_common(10)}
for r in cty:
    if r['keys'][1] in keep: seg.append(dict(site='nd', date=r['keys'][0], dim='Country', value=r['keys'][1].upper(), clicks=r['clicks'], impressions=r['impressions'], pos_w=round(r['position'] * r['impressions'], 1)))
for site in ('fv', 'fo'):
    rows = J(DATA + site + '_dcd.json')['rows']
    for dim, ki, fmt in (('Device', 2, str.title), ('Country', 1, str.upper)):
        tot = C.Counter()
        for r in rows: tot[r['keys'][ki]] += r['impressions']
        keep = {k for k, _ in tot.most_common(10)}
        for (d, v), (c, i, pw) in agg_by([r for r in rows if r['keys'][ki] in keep], lambda r: (r['keys'][0], r['keys'][ki])).items():
            seg.append(dict(site=site, date=d, dim=dim, value=fmt(v), clicks=c, impressions=i, pos_w=round(pw, 1)))
dump('seg_daily', seg)

# ---------------- annotations ----------------
ann = [
 dict(site='all', date='2025-06-30', end='2025-07-17', label='June 2025 core update', kind='Google'),
 dict(site='all', date='2025-08-26', end='2025-09-22', label='August 2025 spam update', kind='Google'),
 dict(site='all', date='2025-12-11', end='2025-12-29', label='December 2025 core update', kind='Google'),
 dict(site='all', date='2026-03-24', end='2026-03-25', label='March 2026 spam update', kind='Google'),
 dict(site='all', date='2026-03-27', end='2026-04-08', label='March 2026 core update', kind='Google'),
 dict(site='all', date='2026-05-21', end='2026-06-02', label='May 2026 core update', kind='Google'),
 dict(site='all', date='2026-09-24', end='2026-10-08', label='September 2026 spam update', kind='Google'),
 dict(site='fv', date='2026-02-01', end=None, label='Search Console data begins', kind='Site'),
 dict(site='fo', date='2026-09-16', end=None, label='New Squarespace site first crawled', kind='Site'),
 dict(site='fo', date='2026-10-05', end=None, label='Search Console data begins', kind='Site'),
 dict(site='sec', date='2026-10-10', end=None, label='GA4 tracking live', kind='Site'),
]
dump('annotations', ann)

# ---------------- tracked keywords ----------------
TR = {'nd': [('fujifilm x100vi', 'Head term for the top page'), ('fujifilm x100vi review', 'Review intent'), ('fujifilm x100vi sample photos', 'Highest-CTR term'), ('fujifilm x half', 'Second review cluster'),
             ('a7cii', 'Big volume, stuck on page 1 bottom'), ('sony a7cii review', 'Review intent'), ('lightroom preset', 'Commercial: preset sales'), ('photo studio canberra', 'Local service demand'),
             ('photography studio canberra', 'Local service demand'), ('canberra photographer', 'Core local term'), ('event photographer canberra', 'Service page target'), ('real estate photography canberra', 'Service page target'),
             ('boudoir photography canberra', 'Service page target'), ('product photography canberra', 'Service page target'), ('brand photographer', 'Service page target')],
      'fv': [('fovea app', 'Brand'), ('fovea ai', 'Brand'), ('aftershoot alternative', 'Competitor alternative'), ('narrative select alternative', 'Competitor alternative'), ('photo mechanic alternative', 'Competitor alternative'),
             ('lightroom culling alternative', 'Competitor alternative'), ('best ai photo culling software 2026', 'Category head term'), ('best photo culling software 2026', 'Category head term'),
             ('pixieset vs pic-time', 'Folio comparison'), ('aftershoot mac', 'Platform intent'), ('photo culling software for mac', 'Platform intent'), ('narrative select pricing 2026', 'Pricing comparison')],
      'fo': [('custom home builders canberra', 'Core service'), ('luxury home builders canberra', 'Core service'), ('home builders canberra', 'Core service'), ('new home builders canberra', 'Core service'),
             ('home renovations canberra', 'Renovations'), ('kitchen renovations canberra', 'Renovations'), ('kitchen and bathroom renovations', 'Renovations'), ('home extensions canberra', 'Extensions'),
             ('builders near me', 'Near-me demand'), ('forte', 'Brand')],
      'sec': [('wedding planner canberra', 'Core service'), ('event planner canberra', 'Core service'), ('wedding photographer canberra', 'Core service'), ('corporate event planning canberra', 'Core service'), ('someone else collective', 'Brand')]}
dump('tracked', [dict(site=s, query=q, why=w) for s, l in TR.items() for q, w in l])

# ---------------- alerts ----------------
alerts = []
last = dt.date.fromisoformat(LAST)
wk_end = last - dt.timedelta(days=(last.weekday() + 1) % 7 or 7) if last.weekday() != 6 else last   # last complete Sunday
wk_start = wk_end - dt.timedelta(days=6)
base_start = wk_start - dt.timedelta(days=28)
def sumrng(rows, a, b, f):
    return sum(r[f] for r in rows if a <= r['date'] <= b)
for site in ('nd', 'fv', 'fo'):
    rows = [r for r in gsc_daily if r['site'] == site]
    cw = sumrng(rows, str(wk_start), str(wk_end), 'clicks'); bw = sumrng(rows, str(base_start), str(wk_start - dt.timedelta(days=1)), 'clicks') / 4
    iw = sumrng(rows, str(wk_start), str(wk_end), 'impressions'); ib = sumrng(rows, str(base_start), str(wk_start - dt.timedelta(days=1)), 'impressions') / 4
    for name, cur, base, minb in (('clicks', cw, bw, 30), ('impressions', iw, ib, 300)):
        if base >= minb:
            ch = (cur - base) / base
            if abs(ch) >= 0.15:
                alerts.append(dict(site=site, date=str(wk_end), severity='warning' if ch < 0 else 'win', kind='Traffic',
                                   title=f'Search {name} {"down" if ch < 0 else "up"} {abs(ch)*100:.0f}% last week',
                                   detail=f'{int(cur):,} {name} in the week {wk_start:%-d %b}–{wk_end:%-d %b} vs a {int(base):,} weekly average over the previous 4 weeks.'))
# query-level page-1 drops/entries (ND, FV)
for site in ('nd', 'fv'):
    for q in J(OUT + 'q_' + site + '.json')[:400]:
        def avgpos(a, b):
            s = [(i, p) for d, i, p in zip(q['d'], q['i'], q['p']) if a <= d <= b and i]
            ti = sum(i for i, _ in s); return (sum(i * p for i, p in s) / ti, ti) if ti else (None, 0)
        p_now, i_now = avgpos(di(str(wk_start)), di(str(wk_end))); p_bef, i_bef = avgpos(di(str(base_start)), di(str(wk_start)) - 1)
        if p_now is None or p_bef is None or i_bef < 80 or i_now < 20: continue
        if p_bef <= 10 and p_now > 10.5:
            alerts.append(dict(site=site, date=str(wk_end), severity='warning', kind='Ranking', title=f'"{q["query"]}" fell off page 1',
                               detail=f'Average position {p_bef:.1f} → {p_now:.1f} ({i_now:,} impressions last week). Ranking page: {q["top_page"]}.'))
        elif p_bef > 10.5 and p_now <= 10 and i_now >= 10:
            alerts.append(dict(site=site, date=str(wk_end), severity='win', kind='Ranking', title=f'"{q["query"]}" reached page 1',
                               detail=f'Average position {p_bef:.1f} → {p_now:.1f}. Ranking page: {q["top_page"]}.'))
# indexing / status alerts from crawl + inspection
for r in J(DATA + 'index_status.json'):
    if not r['indexed']:
        alerts.append(dict(site=r['site'], date=r['checked'], severity='critical' if r['site'] == 'sec' else 'warning', kind='Indexing', title=f'Not indexed: {norm_page(r["url"])}', detail=f'{r["coverage"]}. {r["note"]}'.strip()))
    elif not r['canonical_ok']:
        alerts.append(dict(site=r['site'], date=r['checked'], severity='info', kind='Indexing', title=f'Canonical overridden: {norm_page(r["url"])}', detail=r['note']))
for site in ('nd', 'fv', 'fo'):
    dead = [p for p in J(OUT + 'p_' + site + '.json') if p['status'] == 404 and sum(p['i'])]
    if dead:
        alerts.append(dict(site=site, date=LAST, severity='critical', kind='Errors', title=f'{len(dead)} URLs Google shows now return 404',
                           detail=f'{sum(sum(p["i"]) for p in dead):,} impressions went to pages that return "page not found". Top: ' + ', '.join(norm_page(p['page']) for p in dead[:3]) + '. Add 301 redirects (see Pages → Redirect map).'))
# direct-traffic bot spikes in the last 8 weeks
for site in ('nd', 'fv'):
    rows = [r for r in ga_daily if r['site'] == site]
    vals = [r['direct'] for r in rows]; m = st.mean(vals); sd = st.pstdev(vals)
    for r in rows:
        if r['date'] >= str(last - dt.timedelta(days=56)) and r['direct'] > m + 3 * sd and r['direct'] > 40:
            alerts.append(dict(site=site, date=r['date'], severity='info', kind='Tracking', title=f'Direct-traffic spike on {r["date"]}', detail=f'{r["direct"]} Direct sessions vs a daily average of {m:.0f}. Likely bot traffic inflating sessions.'))
# site-health alerts from the live crawler
if os.path.exists(LIVE + 'crawl_issues.json'):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import crawl
    alerts += crawl.alerts_from_crawl(LIVE, LAST)
dump('alerts', alerts)
print('LAST', LAST, 'alert week', wk_start, wk_end)

# ---------------- empty series for sites without Search Console data ----------------
for site in ('sec',):
    for pre in ('q_', 'p_'):
        if not os.path.exists(OUT + pre + site + '.json'): dump(pre + site, [])
# ---------------- static/curated datasets carried over ----------------
import shutil
for n in ('sites', 'issues', 'competitors', 'gaps', 'actions', 'speed', 'cannibal'):
    shutil.copy(STATIC + n + '.json', OUT + n + '.json')
shutil.copy(DATA + 'index_status.json', OUT + 'index_status.json')
# live crawl datasets, staged in out/crawl/ until the dashboard has views registered for them
if os.path.exists(LIVE + 'crawl_issues.json'):
    os.makedirs(OUT + 'crawl/', exist_ok=True)
    for n in ('crawl_issues', 'crawl_resolved', 'crawl_changes', 'crawl_checks', 'crawl_summary', 'crawl_pages', 'crawl_links', 'crawl_images', 'crawl_robots'):
        if os.path.exists(LIVE + n + '.json'): shutil.copy(LIVE + n + '.json', OUT + 'crawl/' + n + '.json')
