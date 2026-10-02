# Termux ARM64 quickstart

```bash
pkg update -y
pkg install -y git
git clone https://github.com/ghost-dev420/cumbria-public-records-tracker.git
cd cumbria-public-records-tracker
```

Create a short-lived runner token in GitHub under **Settings -> Actions -> Runners -> New self-hosted runner**, choosing **Linux / ARM64**.

Then:

```bash
RUNNER_TOKEN='<short-lived-token>' bash scripts/setup-termux-arm64-runner.sh
bash scripts/start-termux-arm64-runner.sh
```

Once GitHub shows the runner as **Idle**, launch **collect ModernGov on self-hosted browser** from Actions. Keep Termux running during collection.
