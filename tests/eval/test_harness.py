from __future__ import annotations

from pathlib import Path

from recoveryloop.eval.harness import EvalReport, run_eval

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "synthetic_batch.json"


def test_harness_deterministic() -> None:
    report_a = run_eval(DATA)
    report_b = run_eval(DATA)
    assert report_a.model_dump() == report_b.model_dump()


def test_exception_list_not_empty() -> None:
    report = run_eval(DATA)
    assert len(report.exceptions) > 0


def test_metrics_bounded() -> None:
    report = run_eval(DATA)
    assert 0.0 <= report.correct_diagnosis_rate <= 1.0
    assert 0.0 <= report.correct_decision_rate <= 1.0
    assert 0.0 <= report.precision <= 1.0
    assert 0.0 <= report.recall <= 1.0
    assert 0.0 <= report.recovery_rate <= 1.0


def test_summary_text_not_empty() -> None:
    report = run_eval(DATA)
    text = report.summary_text()
    assert "RecoveryLoop Eval Report" in text
    assert "Total cases:" in text
    assert "Attempted recovery:" in text
    assert "Simulated recovered:" in text


def test_json_output(tmp_path: Path) -> None:
    report = run_eval(DATA)
    json_path = tmp_path / "report.json"
    report.to_json(json_path)
    assert json_path.exists()
    assert json_path.stat().st_size > 0


def test_attempted_and_recovered_fields_present() -> None:
    report = run_eval(DATA)
    assert hasattr(report, "attempted_amount")
    assert hasattr(report, "recovered_amount")
    assert report.attempted_amount >= report.recovered_amount
