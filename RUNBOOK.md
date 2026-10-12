# Search Command Centre — weekly refresh runbook

Dashboard: https://claude.ai/artifact/WS6cCJbrWJj2tHLH48a4eG (Artifact type "Dashboard", store-backed).
Owner: Nathan Darma. Timezone: Australia/Sydney.

Every run is a fresh session. Follow these steps exactly. Large tool results are saved to files automatically — copy each saved file into the working directory under the name given.

## 0. Setup
This pipeline lives in the GitHub repository nathandarma/scc-crawler (single source of truth). Download each file from `https://raw.githubusercontent.com/nathandarma/scc-crawler/main/<path>`:
- `mkdir -p raw/static raw/crawl out`
- `build2.py` → `build2.py`, `crawl.py` → `crawl.py` (build2.py imports it)
- `static/<name>.json` → `raw/static/<name>.json` for: crawl_facts, sites, issues, competitors, gaps, actions, speed, cannibal
- `index_urls.json` → `index_urls.json` (URLs to inspect)
If raw.githubusercontent.com can't be reached, stop and tell Nathan; don't fall back to older copies.
- Let END = today − 1 day (YYYY-MM-DD). START = 2025-06-01 (Search Console keeps ~16 months; if START is more than 16 months before END, use END − 486 days and also set BASE in build2.py — leave BASE as 2025-06-01 unless data before it no longer exists).

## 1. Search Console pulls (remote-devices gsc tools, `gsc_search_analytics`, rowLimit 25000)
| Save as | siteUrl | dimensions | notes |
|---|---|---|---|
| raw/nd_qd_0.json, nd_qd_1.json, … | sc-domain:nathandarma.com | ["date","query"] | paginate with startRow 0, 25000, 50000… until a page returns < 25000 rows |
| raw/nd_pd.json | sc-domain:nathandarma.com | ["date","page"] | |
| raw/nd_dev.json | sc-domain:nathandarma.com | ["date","device"] | |
| raw/nd_cty.json | sc-domain:nathandarma.com | ["date","country"] | |
| raw/nd_date.json | sc-domain:nathandarma.com | ["date"] | |
| raw/nd_qp.json | sc-domain:nathandarma.com | ["query","page"] | START = END − 89 days |
| raw/fv_all.json | https://get-fovea.app/ | ["date","query","page","device","country"] | |
| raw/fv_dcd.json | https://get-fovea.app/ | ["date","country","device"] | |
| raw/fo_all.json | sc-domain:fortebuildinggroup.com.au | ["date","query","page","device","country"] | |
| raw/fo_dcd.json | sc-domain:fortebuildinggroup.com.au | ["date","country","device"] | |
For someoneelsecollective.com (sc-domain:someoneelsecollective.com): run ["date"]; if it returns rows, tell the user Google has started showing the site so the build can be extended (q_sec/p_sec are empty placeholders today).
Small results come back inline instead of as a saved file — write those to the named file yourself with the exact JSON returned.

## 2. GA4 pulls (remote-devices analytics-mcp `run_report`, limit 100000)
| Save as | property | dimensions | metrics |
|---|---|---|---|
| raw/ga_nd_daily.json | 343031852 | date, sessionDefaultChannelGroup, deviceCategory | sessions, totalUsers, engagedSessions, keyEvents, screenPageViews, userEngagementDuration |
| raw/ga_fv_daily.json | 522869339 | same | same |
| raw/ga_sec_daily.json | 558207696 | date, sessionDefaultChannelGroup | sessions, engagedSessions, keyEvents |
| raw/ga_nd_lpd.json | 343031852 | date, landingPage, sessionDefaultChannelGroup | sessions, engagedSessions, keyEvents, userEngagementDuration |
| raw/ga_fv_lpd.json | 522869339 | same | same |
Date range for all: START → END.

## 3. Index monitor
For every entry in index_urls.json call `gsc_inspect_url` (siteUrl from the entry). Write raw/index_status.json as a list of
`{site, url, coverage, indexed, last_crawl (YYYY-MM-DD or null), canonical_ok, note, checked: today}` where
indexed = coverage starts with "Submitted and indexed" or "Indexed", canonical_ok = googleCanonical == userCanonical (or either missing),
note = short plain-English note (e.g. "Legacy URL still indexed but now returns 404" for Forte legacy URLs; "Declared canonical is the .html URL; Google chose the clean URL").

## 3b. Live crawl results
The crawler runs daily on GitHub Actions and commits its output to the repo's `data/` folder.
Download each of these from `https://raw.githubusercontent.com/nathandarma/scc-crawler/main/data/<name>.json` into `raw/crawl/<name>.json`:
crawl_facts, crawl_issues, crawl_resolved, crawl_changes, crawl_checks, crawl_summary, crawl_pages, crawl_links, crawl_images, crawl_robots.
If the newest `date` in crawl_summary is more than 2 days before today, tell Nathan the daily crawl has stopped. If the files can't be fetched, skip this step: the build falls back to the static crawl facts.

## 4. Build
`python3 build2.py raw/ out/` — prints one line per dataset. Check `gsc_daily` and `alerts` printed without error.
If Google announced a new core or spam update since the last run (check Search Engine Land / Search Engine Roundtable), add it to the `ann` list in your local build2.py before building, and include the exact line you added in the report so Nathan can commit it to the repository.

## 5. Publish
For each out/<id>.json that is a dataset (every file directly in out/ — leave the out/crawl/ folder unpublished until the dashboard has views for it):
1. Upload: Artifact tool, `url` = dashboard, `asset: true`, `file_path: out/<id>.json` (one call per file).
2. Read the dataset doc: ArtifactData `get`, collection `datasets`, doc_id `<id>` → note `version` and the old `source.url` blob id.
3. ArtifactData `batch` of `update` entries (pinned with if_version) setting `source: {kind:"file", url:"/_blob/<new id>", name:"<id>.json", format:"json"}` and `updated: {at: <ISO now>, by: "Weekly refresh (Claude)"}`.
4. Delete each replaced old blob with Artifact `action: "delete"`, `path: <old id>` — only after the batch succeeded.
Never touch the `files/*` docs (page code) or `board/status` (Nathan's action statuses).

## 6. Report back
Send Nathan a short summary: date range now covered, clicks and impressions for the latest complete week per site vs the 4 weeks before, new critical/warning alerts (from out/alerts.json, including "Site health" alerts from the crawler), any index changes, and what the crawler shows as fixed this week. Keep it to a few lines.
