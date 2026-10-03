# Termux ARM64 quickstart

The Android path does **not** use the GitHub Actions self-hosted runner. ModernGov's interactive HTML pages may be Cloudflare-challenged, so the phone uses the public `mgWebService.asmx` XML endpoints instead. Chromium is not required for this path.

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

Run the full ModernGov XML collection and build the publication-gated site locally:

```bash
bash scripts/run-termux-browser-collector.sh
```

The structured collector deliberately ignores address, email and phone fields from councillor XML when building facts. Raw official responses remain in the private local archive and are not published.

## Optional publishing

Only the generated `site/` directory is permitted to leave the phone. `data/raw`, the DuckDB, review queue and database files are explicitly rejected by both the phone-side publisher and the GitHub-hosted deployment workflow.

Authenticate GitHub once inside the Ubuntu proot:

```bash
proot-distro login ubuntu
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
exit
```

Then publish the sanitized site:

```bash
PUBLISH=1 bash scripts/run-termux-browser-collector.sh
```

That pushes a machine-generated `android-publish` branch containing normal repository files plus the gated `site/`. A GitHub-hosted workflow verifies the privacy boundary again and deploys `site/` to GitHub Pages.

For longer runs, keep Termux alive with its wake lock and exempt it from Android battery optimisation where possible. You can also launch the command inside `tmux`.
