# Termux browser-runner quickstart

Use native Termux only as the launcher. Run the GitHub Actions runner and Playwright inside a Debian ARM64 proot.

## 1. Install Debian in Termux

```bash
pkg update
pkg install -y proot-distro git
proot-distro install debian
termux-wake-lock
proot-distro login debian
```

## 2. Inside Debian, clone and register

Get a short-lived runner token from:

**GitHub repository -> Settings -> Actions -> Runners -> New self-hosted runner -> Linux -> ARM64**

Then run:

```bash
apt-get update
apt-get install -y git ca-certificates

git clone https://github.com/ghost-dev420/cumbria-public-records-tracker.git
cd cumbria-public-records-tracker

RUNNER_TOKEN='<paste-token>' \
RUNNER_NO_SERVICE=1 \
bash scripts/setup-self-hosted-runner.sh
```

## 3. Start the runner

Still inside Debian:

```bash
cd ~/actions-runner-cumbria
RUNNER_ALLOW_RUNASROOT=1 ./run.sh
```

When GitHub reports the runner as **Idle**, manually run the workflow named:

**collect ModernGov on self-hosted browser**

If both access probes succeed, the workflow collects ModernGov through Chromium, builds through the publication gate, and deploys the safe site artifact.

## Notes

- The phone must remain online while the job runs.
- `termux-wake-lock` reduces sleep interruptions.
- Android may still kill Termux unless battery optimisation is disabled for it.
- The scheduled workflow waits for a matching runner to come online; for a phone, manual collection is usually more reliable than expecting a permanent daemon.
- No Cloudflare-challenge bypass, stealth plugin, proxy rotation or CAPTCHA solving is used.
