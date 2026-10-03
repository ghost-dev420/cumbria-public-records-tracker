# ModernGov browser collection

Cumberland and Westmorland & Furness ModernGov can return HTTP 403 responses to GitHub-hosted datacentre traffic. The tracker therefore supports browser collection from a normal self-hosted Linux machine or directly from Android/Termux, while keeping the same evidence and publication rules.

## Security boundary

- No stealth plugins, proxy rotation, CAPTCHA solving, webdriver masking or challenge bypass is used.
- `robots.txt` is still respected.
- Only the publication-gated `site/` output is deployable.
- The DuckDB, `data/raw`, review queue and unresolved matches remain private collection state.
- Pull requests do not execute arbitrary code on a self-hosted machine or Android phone.

## Normal Linux self-hosted runner

The workflow `.github/workflows/collect-moderngov-self-hosted.yml` remains available for a normal Linux x86_64 or ARM64 machine carrying the `cumbria-browser` runner label.

Create a short-lived registration token in GitHub under **Settings -> Actions -> Runners -> New self-hosted runner**, then from a clone of the repository run:

```bash
RUNNER_TOKEN='<short-lived-token>' bash scripts/setup-self-hosted-runner.sh
```

On a normal systemd Linux machine the bootstrap can install the GitHub Actions runner as a service. The workflow is manual/scheduled and is not a pull-request target.

## Android / Termux ARM64

Android does not use the GitHub Actions runner. The runner bundles CoreCLR, which can fail to initialise inside ARM64 Android + proot even when the device has available memory. Instead, Termux hosts an Ubuntu ARM64 proot and runs the existing Python/Playwright collector directly.

From native Termux:

```bash
pkg update -y
pkg install -y git

git clone https://github.com/ghost-dev420/cumbria-public-records-tracker.git
cd cumbria-public-records-tracker

git fetch origin
git reset --hard origin/main
bash scripts/setup-termux-browser-collector.sh
```

Probe both ModernGov sites without starting the full crawl:

```bash
PROBE_ONLY=1 bash scripts/run-termux-browser-collector.sh
```

Run a full local collection and publication-gated build:

```bash
bash scripts/run-termux-browser-collector.sh
```

The phone keeps `data/tracker.duckdb` and `data/raw` inside the Ubuntu proot between runs.

### Publish from Android

Publishing is deliberately separate from collection. Authenticate GitHub once inside the Ubuntu proot using GitHub CLI's browser/device login:

```bash
proot-distro login ubuntu
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
exit
```

Then:

```bash
PUBLISH=1 bash scripts/run-termux-browser-collector.sh
```

`scripts/publish-android-site.sh` creates a fresh clone of `main`, copies only the already publication-gated `site/`, verifies that no raw archive or DuckDB files are present, and pushes the machine-generated `android-publish` branch. `.github/workflows/deploy-android-publish.yml` runs on GitHub-hosted infrastructure, repeats the privacy checks and deploys only `site/` to Pages.

The Android path therefore requires no runner registration token and no CoreCLR process on the phone.
