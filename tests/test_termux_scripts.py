from pathlib import Path


def test_termux_runner_scripts_reference_expected_labels_and_paths() -> None:
    setup = Path("scripts/setup-termux-arm64-runner.sh").read_text()
    start = Path("scripts/start-termux-arm64-runner.sh").read_text()

    assert "cumbria-browser,android-termux" in setup
    assert "proot-distro" in setup
    assert "RUNNER_NO_SERVICE=1" in setup
    assert "/root/cumbria-public-records-tracker" in setup
    assert "/root/actions-runner-cumbria" in start
    assert "./run.sh" in start
