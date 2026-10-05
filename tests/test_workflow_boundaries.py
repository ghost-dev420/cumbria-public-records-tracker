from pathlib import Path


def test_android_publish_is_the_only_pages_deployer():
    workflows = Path(".github/workflows")
    deployers = []
    for path in workflows.glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        if "actions/deploy-pages@" in text:
            deployers.append(path.name)
    assert deployers == ["deploy-android-publish.yml"]


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
