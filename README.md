# COLLEXTOR

COLLEXTOR is a lightweight personal AI newspaper for AI engineering, research, infrastructure, startups, business, and policy. It is tuned for a reader who follows multimodal learning, medical imaging, neuroimaging, MRI, and Alzheimer's disease research, and wants a clean front page during short daily breaks.

Expected public site URL:

`https://tsun0193.github.io/collextor/`

## Architecture in Plain Language

The site is a static GitHub Pages website. The pages are ordinary HTML, CSS, and vanilla JavaScript. They load JSON files from the repository:

- `data/latest.json` powers the Daily page.
- `data/media.json` powers the Media page.
- `data/source-status.json` powers the Sources page.
- `data/stocks.json` powers the Daily market tape.

A small Python script, `scripts/update_feeds.py`, fetches configured sources, normalizes article metadata, applies transparent rule-based ranking, deduplicates similar stories, refreshes curated media feeds, and writes compact JSON.

## Why It Costs Nothing

COLLEXTOR uses only free building blocks:

- GitHub Actions for scheduled updates.
- GitHub Pages for hosting.
- Public RSS, Atom, HTML listing pages, YouTube RSS, and free public APIs.
- Delayed public market quotes from Yahoo Finance chart data.
- Repository JSON files as storage.

There are no API keys, paid APIs, AI or LLM APIs, hosted models, external databases, cookies, analytics, accounts, ads, or tracking.

## Daily Updates

The GitHub Actions workflow runs three times per day. GitHub schedules are UTC, so the workflow converts the intended Asia/Bangkok times as comments in `.github/workflows/update-and-deploy.yml`:

- 06:37 Bangkok = 23:37 UTC on the previous day
- 11:37 Bangkok = 04:37 UTC
- 17:37 Bangkok = 10:37 UTC

Each run installs dependencies, runs unit tests, refreshes feeds, validates JSON, commits changed data when needed, and deploys GitHub Pages in the same run.

To run an update manually in GitHub:

1. Open the repository on GitHub.
2. Go to **Actions**.
3. Choose **Update and deploy COLLEXTOR**.
4. Select **Run workflow**.

## Retention

Daily keeps articles from roughly the last 48 hours. History keeps up to 21 days or about 600 items, whichever limit is hit first. Saved stories live in the browser through local storage, because the site is static and has no account system.

## Enable GitHub Pages

If Pages is not already enabled:

1. Open **Repository Settings**.
2. Go to **Pages**.
3. Set **Source** to **GitHub Actions**.

The workflow already declares the required permissions for content updates and Pages deployment.

## Add or Disable a Source

Edit `config/sources.yaml` in GitHub's web editor.

To disable a source, set:

```yaml
enabled: false
```

To add or adjust a feed, edit fields such as:

```yaml
id: example_source
name: Example Source
enabled: true
source_type: rss
page_url: https://example.com/
feed_url: https://example.com/feed.xml
category: engineering
priority: high
max_items: 10
```

Supported source types are RSS, Atom, public JSON/XML APIs implemented in the script, and explicitly configured lightweight HTML listing adapters.

## Adjust Ranking

Edit `config/ranking.yaml` to change source weights, thresholds, keywords, history limits, and section behavior. The ranking system is rule-based and writes score reasons into the generated data so the result stays inspectable.

## Inspect Failed Workflows

Open **Actions**, choose the failed run, and expand the failing step. Common issues are temporary feed outages, source markup changes, or GitHub Pages not being enabled with GitHub Actions as the source.

If GitHub disables scheduled workflows after prolonged repository inactivity, open the repository's **Actions** tab and re-enable the workflow. You can also run it manually once to confirm it is active.

## Copyright and Source Linking

COLLEXTOR stores titles, publication metadata, author metadata when provided, short feed/API descriptions, thumbnails, and direct links. It does not fetch or store full article bodies, bypass paywalls, or reproduce complete copyrighted articles. All reading links open the original publisher.

## No-AI and Privacy Statement

No AI model, LLM API, local model, hosted model, analytics service, cookies, user account, authentication, tracking pixel, advertisement, external database, or secret is used.

## Known Limitations

- Some publishers do not provide stable public feeds, so they may be marked unavailable or may fail until their adapter is adjusted.
- Feed descriptions are only as good as the publisher-provided metadata.
- Rule-based clustering is intentionally simple and may miss loosely worded duplicates.
- GitHub scheduled workflows can be delayed by GitHub's shared infrastructure.
