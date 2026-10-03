from pathlib import Path


def test_direct_android_collector_uses_full_xml_api_registry_not_actions_runner() -> None:
    setup = Path("scripts/setup-android-browser-ubuntu.sh").read_text()
    run = Path("scripts/run-android-browser-ubuntu.sh").read_text()
    wrapper = Path("scripts/run-termux-browser-collector.sh").read_text()

    assert ".android-venv" in setup
    assert "public_records_tracker.modern_gov_api_probe" in run
    assert "sources-android-full.yml" in run
    assert "COLLECT_CONFIG" in run
    assert "PROBE_ONLY" in run
    assert "proot-distro login" in wrapper
    assert "^ID=ubuntu$" in wrapper
    assert "sources-android-full.yml" in wrapper
    assert "Runner.Listener" not in setup + run + wrapper
    assert "CoreCLR" not in setup + run + wrapper


def test_android_publisher_only_stages_publication_gated_site() -> None:
    publish = Path("scripts/publish-android-site.sh").read_text()

    assert "android-publish" in publish
    assert "git add -f site" in publish
    assert "grep -v '^site/'" in publish
    assert "site/data/raw" in publish
    assert "site/tracker.duckdb" in publish
    assert "*.duckdb" in publish
    assert "gh auth status" in publish


def test_android_publish_workflow_rechecks_privacy_boundary() -> None:
    workflow = Path(".github/workflows/deploy-android-publish.yml").read_text()

    assert "branches:" in workflow
    assert "android-publish" in workflow
    assert "runs-on: ubuntu-latest" in workflow
    assert "site/data/raw" in workflow
    assert "site/tracker.duckdb" in workflow
    assert "site/api/review-queue.json" in workflow
    assert "actions/upload-pages-artifact@v4" in workflow
    assert "actions/deploy-pages@v4" in workflow


def test_termux_quickstart_no_longer_requires_runner_token() -> None:
    quickstart = Path("docs/termux-quickstart.md").read_text()

    assert "setup-termux-browser-collector.sh" in quickstart
    assert "PROBE_ONLY=1" in quickstart
    assert "PUBLISH=1" in quickstart
    assert "RUNNER_TOKEN" not in quickstart
