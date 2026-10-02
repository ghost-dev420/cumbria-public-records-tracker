# Self-hosted ModernGov browser runner

Cumberland and Westmorland & Furness ModernGov currently return Cloudflare HTTP 403 responses to GitHub-hosted runners, including plain Playwright Chromium. The tracker therefore keeps ModernGov browser collection on a dedicated self-hosted Linux runner where the public pages are ordinarily accessible.

## Security boundary

- The self-hosted workflow is triggered only by `workflow_dispatch` and a schedule.
- Pull requests do not run on the self-hosted runner.
- Use a dedicated machine/account where practical.
- The custom runner label is `cumbria-browser`.
- No stealth plugins, proxy rotation, CAPTCHA solving, webdriver masking, or challenge bypass is used.
- `robots.txt` is still respected.
- Only the publication-gated `site/` artifact is deployed; the DuckDB, raw archive, review queue and unresolved matches remain private.

## Supported runner architectures

The workflow is architecture-neutral and accepts any self-hosted Linux runner carrying the `cumbria-browser` label. The bootstrap supports:

- Linux x86_64 / x64
- Linux aarch64 / ARM64

## Normal Linux setup

In GitHub open **Settings -> Actions -> Runners -> New self-hosted runner**, select the matching Linux architecture, and copy the short-lived registration token.

From a clone of this repository on the Linux machine:

```bash
RUNNER_TOKEN='<short-lived-token>' bash scripts/setup-self-hosted-runner.sh
```

On a normal systemd Linux machine the script installs the runner as a service.

## Android / Termux setup

Do not install the GitHub runner directly into native Termux. Android/Termux uses Android's userspace rather than a normal glibc Linux distribution. Use a Debian proot so the runner and Playwright see a supported Linux userspace.

In native Termux:

```bash
pkg update
pkg install -y proot-distro git
proot-distro install debian
termux-wake-lock
proot-distro login debian
```

Then, inside the Debian shell:

```bash
apt-get update
apt-get install -y git ca-certificates

git clone https://github.com/ghost-dev420/cumbria-public-records-tracker.git
cd cumbria-public-records-tracker

RUNNER_TOKEN='<short-lived-token>' \
RUNNER_NO_SERVICE=1 \
bash scripts/setup-self-hosted-runner.sh
```

The proot environment has no normal systemd service manager, so the runner operates in foreground mode.

For the first test you can start it inside Debian:

```bash
cd ~/actions-runner-cumbria
RUNNER_ALLOW_RUNASROOT=1 ./run.sh
```

For later runs, exit back to native Termux and use the repository launcher:

```bash
cd ~/cumbria-public-records-tracker
bash scripts/start-termux-runner.sh
```

If the repository clone exists only inside Debian, either clone a lightweight copy into native Termux for the launcher or run the earlier `proot-distro login debian` command manually.

Keep the Termux/proot session alive while collection runs. `termux-wake-lock` helps keep the CPU awake, but Android battery/process management can still stop Termux. For reliable scheduled daily collection, exempt Termux from battery optimisation where your Android build allows it. For occasional/manual collection, simply start the runner before launching the workflow.

To release the Termux wake lock later:

```bash
termux-wake-unlock
```

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
