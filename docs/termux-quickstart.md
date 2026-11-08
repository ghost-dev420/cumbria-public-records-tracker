# Termux ARM64 quickstart

The Android path does **not** use the GitHub Actions self-hosted runner. ModernGov's interactive HTML pages may be Cloudflare-challenged, so the phone uses the public `mgWebService.asmx` XML endpoints instead. Chromium is not required for this path.

The Android collector now runs the **full production registry**. It inherits the normal source list and swaps only the ModernGov HTML entry points that are blocked by Cloudflare for their structured XML equivalents. This keeps Cumberland, Westmorland and Furness, Workington, Wigton, Whitehaven, procurement, spending/transparency, legacy-authority, grants and Ombudsman/regulatory sources in one run.

In native Termux:

```bash
pkg update -y
pkg install -y git

git clone https://github.com/ghost-dev420/cumbria-public-records-tracker.git
cd cumbria-public-records-tracker

git fetch origin
git reset --hard origin/main
bash scripts/setup-termux-browser-collector.sh
```

The setup creates an Ubuntu proot, installs Python and the tracker, and keeps the evidence database and raw archive locally inside the Ubuntu environment.

Test only whether the phone can reach both ModernGov structured APIs:

```bash
PROBE_ONLY=1 bash scripts/run-termux-browser-collector.sh
```

Run the full production collection and build the publication-gated site locally:

```bash
bash scripts/run-termux-browser-collector.sh
```

A full run is intentionally resilient: one unavailable public source is recorded as a source error but does not discard successfully collected sources. For a diagnostic run that must return non-zero on any source or extraction error, use:

```bash
STRICT=1 bash scripts/run-termux-browser-collector.sh
```

The structured ModernGov collector deliberately ignores address, email and phone fields from councillor XML when building facts. Raw official responses remain in the private local archive and are not published.

## If you are already inside Ubuntu

If your prompt already looks like `root@localhost:...`, do **not** run `proot-distro login ubuntu` again. Run the inner command directly:

```bash
cd /root/cumbria-public-records-tracker
source .android-venv/bin/activate
bash scripts/run-android-browser-ubuntu.sh
```

The Termux wrapper also detects an existing Ubuntu proot and will call the inner collector directly rather than attempting a nested proot session.

## Optional publishing

Only the generated `site/` directory is permitted to leave the phone. `data/raw`, the DuckDB, review queue and database files are explicitly rejected by both the phone-side publisher and the GitHub-hosted deployment workflow.

Authenticate GitHub once inside the Ubuntu proot:

```bash
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
```

Then publish the sanitized site from native Termux:

```bash
PUBLISH=1 bash scripts/run-termux-browser-collector.sh
```

Or, when already inside Ubuntu:

```bash
PUBLISH=1 bash scripts/run-android-browser-ubuntu.sh
```

That pushes a machine-generated `android-publish` branch containing normal repository files plus the gated `site/`. A GitHub-hosted workflow validates the privacy boundary and stores a short-lived audit artifact, but does not deploy it. The public Pages site now deploys exclusively through the GitHub-hosted production workflow.

For longer runs, keep Termux alive with its wake lock and exempt it from Android battery optimisation where possible. You can also launch the command inside `tmux`.

## Automatic daily production schedule (on the phone)

The full `scripts/update-production.sh` pipeline is the production entry point. It backs up the **existing** DuckDB, retains the raw archive, refreshes reference data when due, collects configured sources, analyses, builds the publication-gated site and publishes sanitized `site/` when authenticated. The GitHub-hosted smoke test is not a persistent production database.

After the initial Ubuntu setup and one-time `gh auth login` inside Ubuntu, run from **native Termux** in the cloned repository:

```bash
cd ~/cumbria-public-records-tracker
bash scripts/install-termux-production-cron.sh
```

This schedules a daily run at **04:19 phone-local time**, preserving unrelated cron entries. It keeps using `/root/cumbria-public-records-tracker/data/tracker.duckdb` inside Ubuntu and never uploads the database or `data/raw/`. It fast-forwards its checkout without resetting local work.

Check the automation manually:

```bash
bash scripts/run-termux-production.sh
sv status crond
crontab -l
tail -n 80 ~/.local/state/cumbria/production.log
```

On Android, cron may be killed by sleep or reboot. Keep Termux running, enable a wake lock and a battery optimisation exemption, and install **Termux:Boot** from the same trusted package source as Termux. Launch Termux:Boot once; the installer already creates its boot script. If `sv status crond` isn't running, restart Termux and run `sv-enable crond && sv up crond`. Verify a complete local production log **and** a successful GitHub `deploy sanitized Android collection` workflow before considering production healthy.

The `production Android collection and publish` GitHub Actions workflow no longer schedules daily jobs against an absent `cumbria-browser` runner. It remains manual-only for a real always-online Linux runner.
