import json
import subprocess
import sys
from pathlib import Path

from recoveryloop.schema import ActionType, FailureCode, FailureEvent, GroundTruthLabel

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "generate_dataset.py"
DATA = ROOT / "data" / "synthetic_batch.json"


def _run_generator() -> None:
    subprocess.run(
        [sys.executable, str(SCRIPT)],
        check=True,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def _load() -> list[dict]:
    with open(DATA, encoding="utf-8") as fh:
        return json.load(fh)


def test_generator_is_deterministic() -> None:
    _run_generator()
    first = DATA.read_bytes()
    _run_generator()
    second = DATA.read_bytes()
    assert first == second


def test_exactly_60_cases() -> None:
    _run_generator()
    assert len(_load()) == 60


def test_all_events_validate_against_schema() -> None:
    _run_generator()
    for record in _load():
        FailureEvent(**record["event"])


def test_all_ground_truths_validate_against_schema() -> None:
    _run_generator()
    for record in _load():
        GroundTruthLabel(**record["ground_truth"])


def test_at_least_8_no_action_outside_valid_ptp() -> None:
    _run_generator()
    count = 0
    for record in _load():
        event, label = record["event"], record["ground_truth"]
        if label["expected_action"] != ActionType.no_action.value:
            continue
        if (
            event["has_active_ptp"]
            and event["ptp_date"]
            and event["ptp_date"] > "2026-08-30"
        ):
            continue
        count += 1
    assert count >= 8


def test_at_least_6_adversarial_cases() -> None:
    _run_generator()
    count = sum(1 for r in _load() if r["ground_truth"]["is_adversarial"])
    assert count >= 6


def test_all_failure_codes_present() -> None:
    _run_generator()
    codes = {r["event"]["failure_code"] for r in _load()}
    assert codes == {c.value for c in FailureCode}
