from pathlib import Path


def test_cloud_publication_is_the_only_pages_deployer():
    workflows = Path(".github/workflows")
    deployers = []
    for path in workflows.glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        if "actions/deploy-pages@" in text:
            deployers.append(path.name)
    assert deployers == ["hosted-production.yml"]



def test_cloud_production_never_uploads_raw_or_database_artifacts():
    workflow = Path(".github/workflows/hosted-production.yml").read_text(encoding="utf-8")
    guard = Path("scripts/verify-hosted-site.sh").read_text(encoding="utf-8")
    assert "runs-on: ubuntu-latest" in workflow
    assert "config/sources-android-full.yml" in workflow
    assert "actions/cache@" not in workflow
    assert "actions/upload-artifact@" not in workflow
    assert "actions/upload-pages-artifact@" in workflow
    assert "scripts/verify-hosted-site.sh site" in workflow
    for private_marker in ("review-queue.json", "*.duckdb", "data/raw"):
        assert private_marker in guard


def test_legacy_android_handoff_validates_but_cannot_deploy():
    workflow = Path(".github/workflows/deploy-android-publish.yml").read_text(encoding="utf-8")
    assert "Verify publication boundary" in workflow
    assert "actions/deploy-pages@" not in workflow
    assert "actions/upload-pages-artifact@" not in workflow


def test_collection_smoke_does_not_cache_or_deploy_private_state():
    text = Path(".github/workflows/collect-and-publish.yml").read_text(encoding="utf-8")
    assert "actions/cache@" not in text
    assert "actions/deploy-pages@" not in text
    assert "actions/upload-pages-artifact@" not in text
    assert "actions/upload-artifact@" in text


def test_production_script_supports_analysis_only_republish():
    text = Path("scripts/update-production.sh").read_text(encoding="utf-8")
    assert 'COLLECT="${COLLECT:-1}"' in text
    assert 'if [[ "$COLLECT" == "1" ]]; then' in text
    assert 'if [[ "$COLLECT" == "1" && "$ENABLE_REFERENCE_REFRESH" == "1" ]]; then' in text
    assert 'COLLECT=$COLLECT; skipping source collection and registry refresh.' in text
    assert 'scripts/reanalyze.py' in text
    assert 'prt build-site' in text
    assert 'publish-android-site.sh' in text
