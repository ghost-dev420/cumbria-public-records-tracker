# Self-hosted ModernGov browser runner

Cumberland and Westmorland & Furness ModernGov currently return Cloudflare HTTP 403 responses to GitHub-hosted runners, including plain Playwright Chromium. The tracker therefore keeps ModernGov browser collection on a dedicated self-hosted Linux runner where the public pages are ordinarily accessible.

## Security boundary

- The self-hosted workflow is triggered only by `workflow_dispatch` and a schedule.
- Pull requests do not run on the self-hosted runner.
- The required custom runner label is `cumbria-browser`.
- Android/Termux runners also receive `android-termux`.
- No stealth plugins, proxy rotation, CAPTCHA solving, webdriver masking, or challenge bypass is used.
- `robots.txt` is still respected.
- Only the publication-gated `site/` artifact is deployed; the DuckDB, raw archive, review queue and unresolved matches remain private.

## Supported runner architectures

The collection workflow is architecture-neutral and accepts a self-hosted Linux runner carrying the `cumbria-browser` label. GitHub supports Linux ARM64 self-hosted runners, and the bootstrap supports:

- Linux x86_64 / x64
- Linux aarch64 / ARM64

## Normal Linux setup

In GitHub open **Settings -> Actions -> Runners -> New self-hosted runner**, select the matching Linux architecture, and copy the short-lived registration token.

From a clone of this repository on the Linux machine:

```bash
RUNNER_TOKEN='<short-lived-token>' bash scripts/setup-self-hosted-runner.sh
```

On a normal systemd Linux machine the script installs the runner as a service.

## Android / Termux ARM64 setup

Use native Termux only as the host. The GitHub runner and Playwright run inside an Ubuntu proot, providing the normal Linux userspace they expect.

First clone the repository in Termux:

```bash
pkg update -y
pkg install -y git
git clone https://github.com/ghost-dev420/cumbria-public-records-tracker.git
cd cumbria-public-records-tracker
```

In GitHub open **Settings -> Actions -> Runners -> New self-hosted runner**, select **Linux** and **ARM64**, and copy the short-lived registration token. Then run:

```bash
RUNNER_TOKEN='<short-lived-token>' \
bash scripts/setup-termux-arm64-runner.sh
```

That script:

1. verifies the Android device is ARM64;
2. installs `proot-distro`, Git and tmux in Termux;
3. installs an Ubuntu proot if needed;
4. clones the tracker inside Ubuntu;
5. installs the ARM64 GitHub Actions runner and browser dependencies;
6. registers it with `cumbria-browser,android-termux` labels;
7. leaves it configured for foreground operation because proot has no normal systemd service manager.

Start the runner from the Termux repository clone with:

```bash
bash scripts/start-termux-arm64-runner.sh
```

For a persistent interactive session:

```bash
tmux new -s cumbria-runner 'bash scripts/start-termux-arm64-runner.sh'
```

Detach with **Ctrl-b**, then **d**. Reattach later with:

```bash
tmux attach -t cumbria-runner
```

The launcher requests a Termux wake lock when the `termux-wake-lock` command is available. Android can still stop Termux under aggressive battery management, so exempt Termux from battery optimisation if you want the scheduled daily job to work reliably. For manual collection, simply start the runner before launching the workflow.

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
