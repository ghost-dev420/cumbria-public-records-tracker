# Termux production quickstart (current architecture)

**Do not register a GitHub Actions runner inside Android proot.** The legacy CoreCLR-based ARM64 setup was superseded because CoreCLR can fail to initialise there, leaving scheduled GitHub jobs queued or cancelled. Android production instead runs the collector directly in Ubuntu proot.

See [Termux ARM64 quickstart](termux-quickstart.md). Once the Android collector and optional GitHub CLI publishing are configured, run from **native Termux**:

```bash
cd ~/cumbria-public-records-tracker
bash scripts/install-termux-production-cron.sh
bash scripts/run-termux-production.sh
```

Check `sv status crond` and `~/.local/state/cumbria/production.log`. Install and launch Termux:Boot once if the schedule must restart after boot, and exempt Termux from battery optimisation. The persistent DuckDB and `data/raw/` stay inside Ubuntu; only the sanitized `site/` directory may be published.

The GitHub `production Android collection and publish` workflow is manual-only for an actual Linux self-hosted runner, not Android.
