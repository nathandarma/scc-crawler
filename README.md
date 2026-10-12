# SCC crawler

Daily SEO crawl of nathandarma.com, someoneelsecollective.com, get-fovea.app and fortebuildinggroup.com.au
for the Search Command Centre dashboard. Standard-library Python 3.10+, no installs.

## Setup (once, about 10 minutes)
1. Create a GitHub repository (public is simplest: everything crawled is public, and the weekly refresh can then
   read results without a token). Upload everything in this folder, keeping `.github/workflows/crawl.yml`.
2. Settings → Actions → General → Workflow permissions → "Read and write permissions".
3. Actions tab → "SCC daily crawl" → Run workflow. The first run takes a few minutes and creates `data/`.
4. Replace OWNER/REPO in the runbook's step 3b with the repository path, and save crawl.py, build2.py and RUNBOOK.md
   back to the SEO project docs.

## Run locally
    python3 crawl.py crawl_config.json data/              # all sites
    python3 crawl.py crawl_config.json data/ --site fo    # one site; other sites' results are kept
    python3 -m unittest discover -s tests -t .            # tests

## Editing crawl_config.json
- `max_pages`: crawl cap per site.  `exclude`: extra regexes for URLs to skip.
- `host_checks`: alternate hosts (http, bare/www, Squarespace staging) that must 301 to the main site.
- `redirect_checks`: legacy URLs. Passes when the URL 301s to a working page, or returns 200 itself (rebuilt at the same slug).
  Add `"expect_to": "/path"` to require a specific destination.

## Outputs (data/)
crawl_issues, crawl_resolved, crawl_changes, crawl_checks, crawl_robots, crawl_summary — site health and alerts
crawl_pages — per-URL facts.  crawl_links — internal link graph.  crawl_images — every image with alt/size.
crawl_facts — drop-in replacement for static/crawl_facts.json used by build2.py.

## Repository layout (whole dashboard pipeline)
- `RUNBOOK.md` — weekly refresh steps for the Search Command Centre (the SEO project only holds a pointer to this file)
- `build2.py` — builds dashboard datasets from Search Console, GA4 and crawl data
- `static/` — curated datasets (issues, actions, competitors, gaps, speed, cannibalisation, fallback crawl facts)
- `index_urls.json` — URLs checked with URL Inspection each week
- `crawl.py`, `crawl_config.json`, `tests/`, `.github/workflows/crawl.yml` — the daily crawler
- `data/` — written by the daily crawl; don't edit by hand
