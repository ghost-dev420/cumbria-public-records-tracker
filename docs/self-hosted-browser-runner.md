# Self-hosted ModernGov browser runner

Cumberland and Westmorland & Furness ModernGov currently return Cloudflare HTTP 403 responses to GitHub-hosted runners, including plain Playwright Chromium. The tracker therefore keeps ModernGov browser collection on a dedicated self-hosted Linux runner where the public pages are ordinarily accessible.

## Security boundary

- The self-hosted workflow is triggered only by `workflow_dispatch` and a schedule.
- Pull requests do not run on the self-hosted runner.
- Use a dedicated non-root Linux user/machine where practical.
- The custom runner label is `cumbria-browser`.
- No stealth plugins, proxy rotation, CAPTCHA solving, webdriver masking, or challenge bypass is used.
- `robots.txt` is still respected.

## One-time setup

In GitHub open **Settings -> Actions -> Runners -> New self-hosted runner**, select Linux x64, and copy the short-lived registration token.

From a clone of this repository on the Linux machine:

```bash
RUNNER_TOKEN='<short-lived-token>' bash scripts/setup-self-hosted-runner.sh
```

The script installs the GitHub runner and Chromium OS dependencies, registers it with the `cumbria-browser` label, and starts it as a service.

## Collection workflow

`.github/workflows/collect-moderngov-self-hosted.yml`:

1. restores the same cached DuckDB/raw archive used by the hosted collector;
2. verifies ordinary Chromium access to both ModernGov sites;
3. collects `cumberland_moderngov_structure` and `westmorland_furness_moderngov_structure` through Playwright;
4. runs the existing extractors/entity resolution/detectors;
5. builds through the publication gate;
6. verifies that the raw archive, DuckDB, and review queue are absent from the public artifact;
7. deploys the gated site to GitHub Pages;
8. saves the enriched shared state back to the repository cache.

The scheduled run is serialized with the normal hosted collector through the same `public-records-collection` concurrency group.
