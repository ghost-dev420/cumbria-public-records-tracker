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
