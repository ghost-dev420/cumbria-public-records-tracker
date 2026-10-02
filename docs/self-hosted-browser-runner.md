# Self-hosted ModernGov browser runner

Cumberland and Westmorland & Furness ModernGov currently return Cloudflare HTTP 403 responses to GitHub-hosted runners, including plain Playwright Chromium. The tracker therefore keeps ModernGov browser collection on a dedicated self-hosted Linux runner where the public pages are ordinarily accessible.

## Security boundary

- The self-hosted workflow is triggered only by `workflow_dispatch` and a schedule.
- Pull requests do not run on the self-hosted runner.
- Use a dedicated machine/account where practical.
- The required custom runner label is `cumbria-browser`.
- Android/Termux runners also receive `android-termux`.
- No stealth plugins, proxy rotation, CAPTCHA solving, webdriver masking, or challenge bypass is used.
- `robots.txt` is still respected.
- Only the publication-gated `site/` artifact is deployed; the DuckDB, raw archive, review queue and unresolved matches remain private.

## Supported runner architectures

The workflow is architecture-neutral and accepts any self-hosted Linux runner carrying the `cumbria-browser` label. The bootstrap supports Linux x86_64/x64 and Linux aarch64/ARM64.

## Normal Linux setup

In GitHub open **Settings -> Actions -> Runners -> New self-hosted runner**, select the matching Linux architecture, and copy the short-lived registration token.

From a clone of this repository on the Linux machine:

```bash
RUNNER_TOKEN='<short-lived-token>' bash scripts/setup-self-hosted-runner.sh
```

On a normal systemd Linux machine the script installs the runner as a service.

## Android / Termux ARM64 setup

Native Termux is the host; the GitHub runner and Playwright run inside an Ubuntu ARM64 proot so they see a normal Linux userspace.

In Termux:

```bash
pkg update -y
pkg install -y git
git clone https://github.com/ghost-dev420/cumbria-public-records-tracker.git
cd cumbria-public-records-tracker
```

In GitHub open **Settings -> Actions -> Runners -> New self-hosted runner**, choose **Linux / ARM64**, copy the short-lived registration token, then run:

```bash
RUNNER_TOKEN='<short-lived-token>' \
bash scripts/setup-termux-arm64-runner.sh
```

The script installs `proot-distro`, tmux and Ubuntu, installs the ARM64 GitHub runner and browser dependencies inside Ubuntu, and registers the phone with `cumbria-browser,android-termux` labels.

Start the runner with:

```bash
bash scripts/start-termux-arm64-runner.sh
```

Or keep it in tmux:

```bash
tmux new -s cumbria-runner 'bash scripts/start-termux-arm64-runner.sh'
```

Detach with **Ctrl-b**, then **d**. Reattach with `tmux attach -t cumbria-runner`.

The launcher requests a Termux wake lock when that command is available. Android can still stop Termux under aggressive battery management, so exempt Termux from battery optimisation for reliable scheduled runs. For occasional/manual collection, just start the runner before launching the workflow.

## Collection workflow

`.github/workflows/collect-moderngov-self-hosted.yml`:

1. restores the same cached DuckDB/raw archive used by the hosted collector;
2. verifies ordinary Chromium access to both ModernGov sites;
3. collects `cumberland_moderngov_structure` and `westmorland_furness_moderngov_structure` through Playwright;
4. runs the existing extractors/entity resolution/detectors;
5. builds through the publication gate;
6. verifies that the raw archive, DuckDB and review queue are absent from the public artifact;
7. deploys the gated site to GitHub Pages;
8. saves the enriched shared state back to the repository cache.

The scheduled run is serialized with the normal hosted collector through the same `public-records-collection` concurrency group.
