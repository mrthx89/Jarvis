#!/usr/bin/env python3
"""Offline text-output comparison; never runs ASR or changes configuration."""

import argparse
import json
import math
from pathlib import Path
import unicodedata


def normalize(text):
    """NFC + casefold, replace punctuation/symbols by spaces, collapse whitespace.

    Accents and numbers are preserved; no Indonesian stemming or number expansion.
    """
    text = unicodedata.normalize("NFC", text).casefold()
    return " ".join("".join(
        " " if unicodedata.category(char)[0] in "PS" else char
        for char in text
    ).split())


def edit_distance(reference, hypothesis):
    """Levenshtein distance using linear auxiliary memory."""
    previous = list(range(len(hypothesis) + 1))
    for i, left in enumerate(reference, 1):
        current = [i]
        for j, right in enumerate(hypothesis, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (left != right)))
        previous = current
    return previous[-1]


def score(reference, hypothesis):
    reference, hypothesis = normalize(reference), normalize(hypothesis)
    words, predicted = reference.split(), hypothesis.split()
    word_errors = edit_distance(words, predicted)
    character_errors = edit_distance(reference, hypothesis)
    return {
        "word_errors": word_errors,
        "reference_words": len(words),
        "wer": word_errors / len(words) if words else None,
        "character_errors": character_errors,
        "reference_characters": len(reference),
        "cer": character_errors / len(reference) if reference else None,
        "exact": reference == hypothesis,
    }


def validate(data):
    if not isinstance(data, dict) or data.get("kind") not in (
            "text_fixture", "external_outputs"):
        raise ValueError("kind must be text_fixture or external_outputs")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases must be a nonempty list")
    ids = set()
    expected_labels = None
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("each case must be an object")
        identifier = case.get("id")
        if not isinstance(identifier, str) or not identifier.strip() or identifier in ids:
            raise ValueError("case ids must be nonempty and unique")
        ids.add(identifier)
        category = case.get("category")
        reference = case.get("reference")
        if category not in ("speech", "command", "empty", "noise"):
            raise ValueError("category must be speech, command, empty or noise")
        if not isinstance(reference, str):
            raise ValueError("reference must be text")
        if bool(normalize(reference)) != (category in ("speech", "command")):
            raise ValueError("speech/command need text; empty/noise need empty references")
        outputs = case.get("outputs")
        if not isinstance(outputs, list) or not outputs:
            raise ValueError("outputs must be a nonempty list")
        labels = set()
        for output in outputs:
            if not isinstance(output, dict):
                raise ValueError("each output must be an object")
            label = output.get("configuration")
            if not isinstance(label, str) or not label.strip() or label in labels:
                raise ValueError("configuration labels must be nonempty and unique per case")
            labels.add(label)
            if not isinstance(output.get("hypothesis"), str):
                raise ValueError("hypothesis must be text")
            if "latency_ms" in output:
                value = output["latency_ms"]
                source = output.get("measurement_source")
                if data["kind"] != "external_outputs":
                    raise ValueError("text fixtures cannot include latency measurements")
                if (type(value) not in (int, float) or not math.isfinite(value)
                        or value < 0):
                    raise ValueError("latency_ms must be a finite nonnegative number")
                if not isinstance(source, str) or not source.strip():
                    raise ValueError("latency_ms requires a measurement_source")
        if expected_labels is not None and labels != expected_labels:
            raise ValueError("all cases must contain the same configuration labels")
        expected_labels = labels


def compare(data):
    validate(data)
    configurations = {}
    cases = []
    for case in data["cases"]:
        scores = {}
        for output in case["outputs"]:
            label = output["configuration"]
            metrics = score(case["reference"], output["hypothesis"])
            if "latency_ms" in output:
                metrics.update(latency_ms=output["latency_ms"],
                               measurement_source=output["measurement_source"],
                               measurement_status="externally_supplied_unverified")
            scores[label] = metrics
            totals = configurations.setdefault(label, {
                "word_errors": 0, "reference_words": 0,
                "character_errors": 0, "reference_characters": 0,
                "command_cases": 0, "command_exact_successes": 0,
                "categories": {},
            })
            category = case["category"]
            group = totals["categories"].setdefault(category, {"cases": 0})
            group["cases"] += 1
            if category in ("empty", "noise"):
                group.setdefault("hallucinations", 0)
                group["hallucinations"] += bool(normalize(output["hypothesis"]))
            if metrics["reference_words"]:
                for key in ("word_errors", "reference_words", "character_errors",
                            "reference_characters"):
                    totals[key] += metrics[key]
            if category == "command":
                totals["command_cases"] += 1
                totals["command_exact_successes"] += metrics["exact"]
        cases.append({"id": case["id"], "category": case["category"], "scores": scores})
    for totals in configurations.values():
        totals["wer"] = (totals["word_errors"] / totals["reference_words"]
                         if totals["reference_words"] else None)
        totals["cer"] = (totals["character_errors"] / totals["reference_characters"]
                         if totals["reference_characters"] else None)
    return {
        "kind": data["kind"],
        "notice": "Text-output comparison only; not a real ASR benchmark. "
                  "No ASR or latency measured here; supplied measurements are unverified. "
                  "No model ranking, selection or configuration changes.",
        "metric_policy": "NFC/casefold; punctuation and symbols become spaces; "
                         "whitespace collapsed; CER includes normalized spaces. "
                         "Micro WER/CER exclude empty references (per-case rates null); "
                         "empty/noise hallucination means nonempty normalized output. "
                         "Command success is normalized text equality, not intent accuracy.",
        "configurations": configurations,
        "cases": cases,
    }


def check_self_test(data, report):
    if (data.get("kind") != "text_fixture"
            or report["configurations"] != data.get("expected_summary")):
        raise ValueError("self-test summary does not match deterministic text fixture")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog='Input: {"kind":"external_outputs","cases":[{"id":"c1",'
               '"category":"command","reference":"buka pintu","outputs":['
               '{"configuration":"your-label","hypothesis":"buka pintu"}]}]}. '
               'Categories: speech, command, empty, noise. All cases need the same '
               'configuration labels. Optional output latency_ms must be a real '
               'externally measured nonnegative duration with measurement_source '
               '(log/run reference and measurement scope); never invent values. '
               'Measurements are forwarded unverified, not timed or aggregated here. '
               'Bundled fixtures contain no measurements. No recordings are read.')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-test", action="store_true",
                      help="check bundled deterministic text fixtures, not ASR")
    mode.add_argument("--input", type=Path,
                      help="UTF-8 JSON containing externally supplied text outputs")
    args = parser.parse_args()
    path = (Path(__file__).resolve().parents[2] / "docs/fixtures/stt_compare.json"
            if args.self_test else args.input)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        report = compare(data)
        if args.self_test:
            check_self_test(data, report)
            report["self_test"] = "passed"
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
