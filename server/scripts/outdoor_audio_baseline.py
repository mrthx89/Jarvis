"""Offline PCM accounting. Does not open devices, sockets, or audio files."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct


RATE = 16000
MAX_BYTES = 640000


def summarize_pcm(chunks):
    """Account across arbitrary read boundaries; retain only one carry byte."""
    digest = hashlib.sha256()
    carry = b""
    samples = squares = peak = clipped = byte_count = 0
    for chunk in chunks:
        digest.update(chunk)
        byte_count += len(chunk)
        block = carry + chunk
        end = len(block) - len(block) % 2
        for (sample,) in struct.iter_unpack("<h", block[:end]):
            samples += 1
            squares += sample * sample
            peak = max(peak, abs(sample))
            clipped += sample in (-32768, 32767)
        carry = block[end:]
    if carry:
        raise ValueError("unaligned_pcm")
    rms = math.sqrt(squares / samples) / 32768 if samples else 0
    return {"bytes": byte_count, "samples": samples,
            "duration_ms": samples * 1000 / RATE, "peak": peak,
            "clipped_samples": clipped,
            "rms_dbfs": 20 * math.log10(rms) if rms else None,
            "sha256": digest.hexdigest()}


def validate_upload(begin, frames, commit):
    """Validate public synthetic vectors, not a network admission/dispatch engine."""
    import uuid

    total = begin["total_samples"]
    if type(total) is not int or not 1 <= total <= MAX_BYTES // 2:
        raise ValueError("invalid_length")
    expected_id = uuid.UUID(begin["utterance_id"]).bytes
    pcm = bytearray()
    for frame in frames:
        if len(frame) < 28:
            raise ValueError("invalid_message")
        magic, identity, offset, size = struct.unpack(">4s16sII", frame[:28])
        if magic != b"NXA2":
            raise ValueError("invalid_message")
        if identity != expected_id:
            raise ValueError("id_conflict")
        payload = frame[28:]
        if size != len(payload) or not 2 <= size <= 16384 or size % 2:
            raise ValueError("invalid_length")
        start = offset * 2
        if start > len(pcm):
            raise ValueError("sequence_gap")
        if start + size > total * 2:
            raise ValueError("invalid_length")
        if start < len(pcm):
            if start + size > len(pcm) or pcm[start:start + size] != payload:
                raise ValueError("overlap_conflict")
            continue
        pcm.extend(payload)
    if len(pcm) != total * 2 or type(commit["total_samples"]) is not int or commit["total_samples"] != total:
        raise ValueError("invalid_length")
    if commit["utterance_id"] != begin["utterance_id"]:
        raise ValueError("id_conflict")
    metrics = summarize_pcm([pcm])
    if begin["sha256"] != metrics["sha256"] or commit["sha256"] != metrics["sha256"]:
        raise ValueError("checksum_mismatch")
    return metrics


def vad_smoke(model_path):
    """Exercise pinned upstream model on silence and seeded noise, never recordings."""
    import time
    import numpy as np
    import onnxruntime as ort

    model = Path(model_path).read_bytes()
    digest = hashlib.sha256(model).hexdigest()
    if digest != "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3":
        raise ValueError("model_checksum_mismatch")
    options = ort.SessionOptions()
    options.intra_op_num_threads = options.inter_op_num_threads = 1
    session = ort.InferenceSession(model, options, providers=["CPUExecutionProvider"])
    state = np.zeros((2, 1, 128), dtype=np.float32)
    context = np.zeros((1, 64), dtype=np.float32)
    rng = np.random.default_rng(2026)
    timings, probabilities = [], []
    for index in range(220):
        window = (np.zeros((1, 512), dtype=np.float32) if index < 110 else
                  rng.normal(0, 0.01, (1, 512)).astype(np.float32))
        inputs = {"input": np.concatenate([context, window], axis=1),
                  "state": state, "sr": np.array(16000, dtype=np.int64)}
        started = time.perf_counter_ns()
        output, state = session.run(None, inputs)
        elapsed = (time.perf_counter_ns() - started) / 1e6
        context = window[:, -64:].copy()
        if index >= 20:
            timings.append(elapsed)
            probabilities.append(float(output.item()))
    return {"evidence_kind": "host_synthetic_not_android", "ort_version": ort.__version__,
            "model_sha256": digest, "model_bytes": len(model), "windows": len(timings),
            "state_shape": list(state.shape), "input_shape": [1, 576],
            "p95_ms": float(np.percentile(timings, 95)),
            "p99_ms": float(np.percentile(timings, 99)),
            "probability_min": min(probabilities), "probability_max": max(probabilities),
            "device_budget": "pending", "speech_accuracy": "not_measured"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", required=True,
                        help="Run synthetic data only; never capture user audio")
    parser.add_argument("--vad-model", type=Path,
                        help="Optional pinned public model for host-only synthetic smoke")
    args = parser.parse_args()
    pcm = struct.pack("<8h", 0, 1, -1, 32767, -32768, 100, -100, 0)
    full = summarize_pcm([pcm])
    split = summarize_pcm([pcm[:1], pcm[1:7], pcm[7:]])
    assert full == split
    fixture_path = Path(__file__).resolve().parents[2] / "docs/fixtures/outdoor_audio_v2.json"
    fixture = json.loads(fixture_path.read_text())
    upload = fixture["upload"]
    metrics = validate_upload(upload["begin"],
                              [bytes.fromhex(value) for value in upload["frames_hex"]],
                              upload["commit"])
    for key, expected in upload["metrics"].items():
        assert metrics[key] == expected
    report = {"evidence_kind": "synthetic_host_only", "metrics": full,
              "partial_read_integrity": "pass", "fixture_integrity": "pass",
              "device_evidence": "pending"}
    if args.vad_model:
        report["vad_smoke"] = vad_smoke(args.vad_model)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
