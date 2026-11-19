# Study Motivator — Source Manager & PWA setup

## What changed
- Info Center now has a Source Manager for adding HTTPS websites and RSS feeds.
- Added `sources.json` for sources collected by GitHub Actions.
- The collector checks configured sources and keeps source attribution in `updates.json`.
- GitHub Actions is scheduled every five hours. GitHub may delay scheduled runs.
- Optional AI source discovery uses Gemini web grounding in GitHub Actions.
- Added a PWA manifest, service worker and SVG app icon for browser installation.

## Enable AI source discovery
1. Open the repository on GitHub.
2. Go to **Settings → Secrets and variables → Actions**.
3. Under **Repository secrets**, choose **New repository secret**.
4. Name it exactly `GEMINI_API_KEY` and paste a Gemini API key as its value.
5. Do not put the key in `index.html`, `sources.json`, or any committed file.
6. Run **Actions → Update HSC info → Run workflow** once.

Without this secret, the normal official-site and Google News collection still runs; AI discovery is skipped.

## Add a source
1. Open Info Center → **উৎস পরিচালনা**.
2. Enter a name, HTTPS URL, and choose Website or RSS.
3. Press **উৎস যোগ করো**, then **Export sources.json**.
4. Replace the repository's `sources.json` with the exported file and commit it to the default branch (or ask for a PR).
5. The next scheduled workflow collects it.

The in-app source list is local to that browser until exported and committed. A static GitHub Pages app cannot safely write directly to the repository without authentication, so no GitHub token is stored in the browser.

## Facebook
Public Facebook pages are not scraped by this project. Use an official RSS feed or a supported public website instead. Graph API integration requires a Meta app and approved permissions.

## Install as an app on Windows
1. Publish the site over HTTPS (GitHub Pages is suitable).
2. Open it in current Microsoft Edge or Chrome.
3. Use the browser's **Install this site as an app** option.
4. The PWA can cache the app shell for offline opening; live notices still need internet.

## Important limitations
- A five-hour GitHub Actions schedule is approximate, not a guaranteed exact interval.
- Opening the app refreshes the published `updates.json`; it does not force a new GitHub Actions run.
- AI-discovered sources are candidates derived from web search grounding. Check their contents and reliability; unofficial sources are labeled as such in the Info Center.
- This project does not automatically publish a native .exe. PWA installation provides a Windows app-like experience.
