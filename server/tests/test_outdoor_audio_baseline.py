"""Offline synthetic checks only; no recorder, provider, or credentials."""
import importlib.util
import copy
import json
import subprocess
import sys
from pathlib import Path
import struct

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "server/scripts/outdoor_audio_baseline.py"


def load_tool():
    assert SCRIPT.exists(), "privacy-safe baseline tool has not been implemented"
    spec = importlib.util.spec_from_file_location("outdoor_audio_baseline", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pcm_metrics_preserve_partial_read_bytes():
    tool = load_tool()
    pcm = struct.pack("<5h", 0, 32767, -32768, 1, -1)
    metrics = tool.summarize_pcm([pcm[:1], pcm[1:7], pcm[7:]])
    assert metrics["samples"] == 5
    assert metrics["duration_ms"] == 0.3125
    assert metrics["peak"] == 32768
    assert metrics["clipped_samples"] == 2
    assert metrics["sha256"] == tool.summarize_pcm([pcm])["sha256"]
    assert metrics["rms_dbfs"] == pytest.approx(-3.97953, abs=0.001)


def test_golden_upload_and_exact_duplicate_are_accounted_once():
    tool = load_tool()
    fixture = json.loads((ROOT / "docs/fixtures/outdoor_audio_v2.json").read_text())
    upload = fixture["upload"]
    frames = [bytes.fromhex(value) for value in upload["frames_hex"]]
    validate = getattr(tool, "validate_upload", None)
    assert callable(validate), "binary fixture integrity checker missing"
    metrics = validate(upload["begin"], frames + frames, upload["commit"])
    for key, expected in upload["metrics"].items():
        assert metrics[key] == expected
    assert metrics["sha256"] == upload["accepted"]["sha256"]


FIXTURE = json.loads((ROOT / "docs/fixtures/outdoor_audio_v2.json").read_text())


@pytest.mark.parametrize("vector", FIXTURE["invalid_upload_vectors"], ids=lambda v: v["name"])
def test_invalid_upload_is_explicit_not_partial_success(vector):
    tool = load_tool()
    upload = copy.deepcopy(FIXTURE["upload"])
    begin, commit = upload["begin"], upload["commit"]
    frame = bytearray.fromhex(upload["frames_hex"][0])
    frames = [frame]
    mutation = vector["mutation"]
    if mutation == "boolean_samples":
        begin["total_samples"] = True
    elif mutation == "zero_samples":
        begin["total_samples"] = 0
    elif mutation == "too_many_samples":
        begin["total_samples"] = 320001
    elif mutation == "gap":
        frame[20:24] = struct.pack(">I", 1)
    elif mutation == "odd_payload":
        frame.pop()
        frame[24:28] = struct.pack(">I", 15)
    elif mutation == "short_header":
        frames = [frame[:27]]
    elif mutation == "wrong_id":
        frame[19] ^= 1
    elif mutation == "checksum":
        frame[-1] ^= 1
    elif mutation == "conflicting_duplicate":
        changed = bytearray(frame)
        changed[-1] ^= 1
        frames.append(changed)
    elif mutation == "incomplete":
        frames = []
    else:
        pytest.fail("unhandled fixture mutation")
    with pytest.raises(ValueError, match="^" + vector["expected_code"] + "$"):
        tool.validate_upload(begin, frames, commit)


def test_self_test_cli_checks_fixture_without_audio_files(tmp_path):
    result = subprocess.run([sys.executable, str(SCRIPT), "--self-test"],
                            cwd=tmp_path, capture_output=True, text=True, check=True)
    report = json.loads(result.stdout)
    assert report["evidence_kind"] == "synthetic_host_only"
    assert report["fixture_integrity"] == "pass"
    assert report["device_evidence"] == "pending"
    assert list(tmp_path.iterdir()) == []


def test_final_odd_byte_is_not_silently_dropped():
    with pytest.raises(ValueError, match="^unaligned_pcm$"):
        load_tool().summarize_pcm([b"\x00"])


def test_empty_synthetic_metrics_use_null_not_infinite_dbfs():
    metrics = load_tool().summarize_pcm([])
    assert metrics["samples"] == 0
    assert metrics["rms_dbfs"] is None
    assert metrics["clipped_samples"] == 0


MODEL = ROOT / "server/.pytest_cache/silero-v6.2.3.onnx"


@pytest.mark.skipif(not MODEL.exists(), reason="optional pinned VAD model not downloaded")
def test_pinned_vad_smoke_uses_real_runtime_and_synthetic_input_only():
    tool = load_tool()
    run = getattr(tool, "vad_smoke", None)
    assert callable(run), "host VAD smoke probe missing"
    report = run(MODEL)
    assert report["evidence_kind"] == "host_synthetic_not_android"
    assert report["model_bytes"] == 2327524
    assert report["windows"] == 200
    assert report["state_shape"] == [2, 1, 128]
    assert 0 <= report["probability_min"] <= report["probability_max"] <= 1
    assert report["p95_ms"] > 0


def test_fixtures_are_bounded_unique_and_privacy_safe():
    assert len(FIXTURE["ownership_traces"]) == 16
    assert len(FIXTURE["exit_paths"]) == 12
    for category in ("ownership_traces", "retry_traces", "invalid_upload_vectors", "hello_vectors"):
        names = [item["name"] for item in FIXTURE[category]]
        assert len(names) == len(set(names))
    assert FIXTURE["limits"]["max_samples"] * 2 == 640000
    assert FIXTURE["limits"]["acquire_deadline_ms"] > FIXTURE["limits"]["quiesce_deadline_ms"]
    for vector in FIXTURE["hello_vectors"]:
        assert len(json.dumps(vector["client"]).encode()) <= 4096
        assert len(json.dumps(vector["server"]).encode()) <= 4096
