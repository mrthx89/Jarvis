import copy
import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "server/scripts/compare_stt.py"


def api():
    return runpy.run_path(str(SCRIPT))


def test_normalizes_indonesian_unicode_without_dropping_accents_or_digits():
    normalize = api()["normalize"]
    assert normalize("  NYALAKAN\tLampu, dapur!  ") == "nyalakan lampu dapur"
    assert normalize("Kafe\u0301 — suhu 27°C") == "kafé suhu 27 c"
    assert normalize("anak-anak") == "anak anak"
    assert normalize(" \n") == ""


def test_word_and_character_edit_errors_allow_insertions_above_one_hundred_percent():
    score = api()["score"]
    result = score("satu dua tiga", "satu empat tiga lima")
    assert result["word_errors"] == 2
    assert result["reference_words"] == 3
    assert result["wer"] == 2 / 3
    chars = score("abc", "axcd")
    assert chars["character_errors"] == 2
    assert chars["reference_characters"] == 3
    assert chars["cer"] == 2 / 3
    assert score("a", "a b c")["wer"] == 2
    assert score("satu dua", "")["word_errors"] == 2
    assert score("é", "e")["character_errors"] == 1
    assert score("  NYALAKAN!", "nyalakan")["exact"] is True


def dataset():
    return {"kind": "external_outputs", "cases": [
        {"id": "command", "category": "command", "reference": "Buka pintu",
         "outputs": [{"configuration": "manual-A", "hypothesis": "BUKA pintu!"}]},
        {"id": "speech", "category": "speech", "reference": "satu dua tiga",
         "outputs": [{"configuration": "manual-A", "hypothesis": "satu tiga"}]},
        {"id": "empty", "category": "empty", "reference": "",
         "outputs": [{"configuration": "manual-A", "hypothesis": ""}]},
        {"id": "noise", "category": "noise", "reference": "",
         "outputs": [{"configuration": "manual-A", "hypothesis": "terima kasih"}]},
    ]}


def test_categories_separate_empty_noise_hallucinations_from_micro_error_rates():
    module = api()
    assert module["score"]("", "terima kasih")["wer"] is None
    assert module["score"]("", "")["cer"] is None
    report = module["compare"](dataset())
    config = report["configurations"]["manual-A"]
    assert config["wer"] == 1 / 5
    assert config["word_errors"] == 1
    assert config["reference_words"] == 5
    assert config["command_exact_successes"] == 1
    assert config["command_cases"] == 1
    assert config["categories"]["empty"] == {"cases": 1, "hallucinations": 0}
    assert config["categories"]["noise"] == {"cases": 1, "hallucinations": 1}
    assert report["cases"][3]["scores"]["manual-A"]["word_errors"] == 2
    empty = dataset()
    empty["cases"] = empty["cases"][2:]
    assert module["compare"](empty)["configurations"]["manual-A"]["wer"] is None


def test_rejects_malformed_or_unpaired_inputs_instead_of_comparing_different_corpora():
    compare = api()["compare"]
    invalid = [None, {}, {"kind": "external_outputs", "cases": []}]
    for field, value in (("reference", 12), ("category", "unknown"), ("id", "")):
        data = dataset()
        data["cases"][0][field] = value
        invalid.append(data)
    noise = dataset()
    noise["cases"][3]["reference"] = "speech"
    invalid.append(noise)
    blank = dataset()
    blank["cases"][0]["reference"] = "..."
    invalid.append(blank)
    duplicate = dataset()
    duplicate["cases"][1]["id"] = "command"
    invalid.append(duplicate)
    for field, value in (("configuration", ""), ("hypothesis", None)):
        data = dataset()
        data["cases"][0]["outputs"][0][field] = value
        invalid.append(data)
    unpaired = dataset()
    unpaired["cases"][1]["outputs"][0]["configuration"] = "different"
    invalid.append(unpaired)
    duplicate_output = dataset()
    duplicate_output["cases"][0]["outputs"] *= 2
    invalid.append(duplicate_output)
    for data in invalid:
        with pytest.raises(ValueError):
            compare(data)
    original = dataset()
    snapshot = copy.deepcopy(original)
    compare(original)
    assert original == snapshot


def test_latency_is_only_forwarded_when_external_with_provenance_and_never_timed_here():
    compare = api()["compare"]
    plain = compare(dataset())
    assert "latency_ms" not in str(plain)
    data = dataset()
    # Schema examples only, not measured ASR performance.
    data["cases"][0]["outputs"][0].update(
        latency_ms=12.5, measurement_source="caller measurement log: run-123")
    measured = compare(data)["cases"][0]["scores"]["manual-A"]
    assert measured["latency_ms"] == 12.5
    assert measured["measurement_source"] == "caller measurement log: run-123"
    assert measured["measurement_status"] == "externally_supplied_unverified"
    for value in (-1, True, "12.5", float("nan"), float("inf"), None):
        bad = copy.deepcopy(data)
        bad["cases"][0]["outputs"][0]["latency_ms"] = value
        with pytest.raises(ValueError):
            compare(bad)
    for source in (None, "", "  ", 42):
        bad = copy.deepcopy(data)
        bad["cases"][0]["outputs"][0]["measurement_source"] = source
        with pytest.raises(ValueError):
            compare(bad)
    fixture = copy.deepcopy(data)
    fixture["kind"] = "text_fixture"
    with pytest.raises(ValueError):
        compare(fixture)


def test_self_test_cli_is_deterministic_text_only_and_checks_expected_metrics():
    command = [sys.executable, str(SCRIPT), "--self-test"]
    first = subprocess.run(command, capture_output=True, text=True, check=False)
    assert first.returncode == 0, first.stderr
    report = json.loads(first.stdout)
    assert report["kind"] == "text_fixture"
    assert report["self_test"] == "passed"
    assert "not a real ASR benchmark" in report["notice"]
    assert "latency_ms" not in first.stdout
    assert report["configurations"]["text-control"]["wer"] == 0
    assert report["configurations"]["text-errors"]["wer"] == 2 / 5
    second = subprocess.run(command, capture_output=True, text=True, check=False)
    assert second.stdout == first.stdout
    fixture = json.loads((ROOT / "docs/fixtures/stt_compare.json").read_text("utf-8"))
    module = api()
    module["check_self_test"](fixture, module["compare"](fixture))
    fixture["expected_summary"]["text-control"]["word_errors"] = 123
    with pytest.raises(ValueError, match="self-test"):
        module["check_self_test"](fixture, module["compare"](fixture))


def test_external_json_cli_supports_labels_utf8_and_reports_input_errors(tmp_path):
    source = tmp_path / "outputs.json"
    data = dataset()
    for case in data["cases"]:
        output = case["outputs"][0]
        output["configuration"] = "konfigurasi-é"
        case["outputs"].append({"configuration": "kontrol", "hypothesis": case["reference"]})
    source.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    command = [sys.executable, str(SCRIPT), "--input", str(source)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["kind"] == "external_outputs"
    assert "self_test" not in report
    assert "konfigurasi-é" in result.stdout
    assert report["configurations"]["kontrol"]["wer"] == 0
    source.write_text('{"kind": ', encoding="utf-8")
    invalid = subprocess.run(command, capture_output=True, text=True, check=False)
    assert invalid.returncode == 2
    assert "error:" in invalid.stderr
    assert "Traceback" not in invalid.stderr
    missing = subprocess.run(command + ["--self-test"], capture_output=True, text=True, check=False)
    assert missing.returncode == 2
    source.unlink()
    absent = subprocess.run(command, capture_output=True, text=True, check=False)
    assert absent.returncode == 2
    assert "Traceback" not in absent.stderr
    help_result = subprocess.run([sys.executable, str(SCRIPT), "--help"],
                                 capture_output=True, text=True, check=False)
    assert "measurement_source" in help_result.stdout
