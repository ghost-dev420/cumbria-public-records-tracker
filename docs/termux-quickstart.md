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

That pushes a machine-generated `android-publish` branch containing normal repository files plus the gated `site/`. A GitHub-hosted workflow verifies the privacy boundary again and deploys `site/` to GitHub Pages.

For longer runs, keep Termux alive with its wake lock and exempt it from Android battery optimisation where possible. You can also launch the command inside `tmux`.
