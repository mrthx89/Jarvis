"""Host execution of SDK-free production Java, not an Android build/test."""
import os
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "android/app/src/main/java/id/my/yhdev/nexo/audio/core"
HARNESS = ROOT / "android/host-tests/AudioCoreHarness.java"


@pytest.fixture(scope="module")
def java_core(tmp_path_factory):
    javac = os.environ.get("NEXO_HOST_JAVAC") or shutil.which("javac")
    if not javac:
        candidates = sorted((ROOT / "server/.pytest_cache").glob("jdk*/bin/javac"))
        javac = str(candidates[-1]) if candidates else None
    if not javac:
        pytest.skip("JDK absent: SDK-free Java host harness not executed")
    out = tmp_path_factory.mktemp("nexo-java-host")
    sources = sorted(str(p) for p in CORE.glob("*.java"))
    built = subprocess.run([javac, "-Xlint:all", "-Werror", "-d", str(out), *sources, *sorted(str(p) for p in HARNESS.parent.glob("*.java") if p.name != "SileroHarness.java")],
                           capture_output=True, text=True, timeout=30)
    assert built.returncode == 0, built.stdout + built.stderr
    assert not built.stderr
    return str(Path(javac).with_name("java")), out


@pytest.mark.parametrize("scenario", ["handoff", "utterance", "vad", "upload", "capture", "local", "lease", "generation", "diagnostic", "ownership", "output_revocation", "lifecycle_admission", "processing_deadline"])
def test_production_audio_core(java_core, scenario):
    java, out = java_core
    harness = {"upload": "UploadHarness", "capture": "CaptureHarness", "local": "LocalCaptureHarness", "lease": "LeaseHarness", "generation": "GenerationHarness", "diagnostic": "DiagnosticHarness", "ownership": "OwnershipHarness", "output_revocation": "OutputRevocationHarness", "lifecycle_admission": "LifecycleAdmissionHarness", "processing_deadline": "ProcessingDeadlineHarness"}.get(scenario, "AudioCoreHarness")
    result = subprocess.run([java, "-ea", "-cp", str(out), harness, scenario,
                             str(ROOT / "docs/fixtures/outdoor_audio_v2.json")],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == f"PASS {scenario}"


def test_shared_fixture_with_production_encoder(java_core):
    java, out = java_core
    fixture = json.loads((ROOT / "docs/fixtures/outdoor_audio_v2.json").read_text())
    upload = fixture["upload"]
    result = subprocess.run([java, "-ea", "-cp", str(out), "FixtureHarness",
                             fixture["ids"]["utterance_id"], upload["pcm_hex"],
                             upload["frames_hex"][0], upload["begin"]["sha256"]],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "PASS fixture"


def test_pinned_silero_production_adapter(java_core):
    java, out = java_core
    jar = ROOT / "server/.pytest_cache/ort-host-1.20.0.jar"
    model = ROOT / "server/.pytest_cache/silero-v6.2.3.onnx"
    if not jar.exists() or not model.exists():
        pytest.skip("Pinned public host ORT/model absent; no implicit download")
    javac = str(Path(java).with_name("javac"))
    adapter = CORE.parent / "vad/SileroVad.java"
    compiled = subprocess.run([javac, "-Xlint:all", "-Werror", "-cp", f"{jar}:{out}", "-d", str(out),
                               str(adapter), str(HARNESS.parent / "SileroHarness.java")],
                              capture_output=True, text=True, timeout=30)
    assert compiled.returncode == 0, compiled.stderr
    result = subprocess.run([java, "-ea", "-cp", f"{jar}:{out}", "SileroHarness", str(model)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "PASS silero"
