"""Sentence chunking utilities for streaming Hermes Agent output to TTS."""

import re
from typing import List, Optional, Tuple

BANNER_PREFIXES = (
    "↻ Resumed session",
    "Session local-",
    "Model restored from session:",
)

FOOTER_PREFIXES = (
    "session_id:",
)

COMMON_ABBREVIATIONS = {
    "dr", "prof", "mr", "mrs", "ms", "jl", "pt", "cv", "no", "dll",
    "dsb", "dst", "etc", "eg", "ie", "st", "ir", "drs", "dra"
}

# Regex to match punctuation (. ? !) or newline
_DELIMITER_REGEX = re.compile(r'([.?!]+[\'")\]]?)(?:\s+|\Z)|(\n+)')


def clean_line_is_banner_or_footer(line: str) -> bool:
    """Check if a stripped line is a session banner or footer."""
    trimmed = line.strip()
    return any(trimmed.startswith(p) for p in BANNER_PREFIXES) or any(trimmed.startswith(p) for p in FOOTER_PREFIXES)


def extract_next_sentence(buf: str, at_eof: bool = False) -> Tuple[Optional[str], str]:
    """Extract the first complete sentence chunk from a streaming buffer.

    Returns:
        (sentence, remaining_buffer): If a sentence is found, returns the sentence
        string and the remaining buffer. If no complete sentence is ready, returns
        (None, buf).
    """
    # 1. Clean leading banners, footers, or empty lines
    while True:
        nl_pos = buf.find("\n")
        if nl_pos != -1:
            line = buf[:nl_pos]
            if clean_line_is_banner_or_footer(line) or not line.strip():
                buf = buf[nl_pos + 1:]
                continue
        elif at_eof:
            line = buf.strip()
            if clean_line_is_banner_or_footer(line):
                return None, ""
        break

    if not buf:
        return None, ""

    pos = 0
    while True:
        m = _DELIMITER_REGEX.search(buf, pos)
        if not m:
            if at_eof:
                rest = buf.strip()
                if clean_line_is_banner_or_footer(rest):
                    return None, ""
                if rest:
                    return rest, ""
            return None, buf

        end_idx = m.end()
        # Candidate text including punctuation
        if m.group(1):
            candidate = buf[: m.start(1) + len(m.group(1))]
            punct = m.group(1)
        else:
            candidate = buf[: m.start(2)]
            punct = None

        candidate_stripped = candidate.strip()

        # Check if punctuation is '.' and should not split (decimal, list, or abbreviation)
        if punct and punct.startswith("."):
            match_start = m.start(1)
            # 1. Decimal number (e.g., 3.14)
            if match_start > 0 and buf[match_start - 1].isdigit():
                if match_start + 1 < len(buf) and buf[match_start + 1].isdigit():
                    pos = m.end()
                    continue
                # Ordered list item at start (e.g., "1. ")
                words = candidate_stripped.split()
                if words and words[-1].rstrip(".").isdigit() and len(words) == 1:
                    pos = m.end()
                    continue

            # 2. Common abbreviations (e.g., "Dr.", "Jl.", "dll.")
            words = candidate_stripped.split()
            if words:
                last_word = words[-1].rstrip(".").lower()
                if last_word in COMMON_ABBREVIATIONS:
                    pos = m.end()
                    continue

        if candidate_stripped:
            remaining = buf[end_idx:]
            return candidate_stripped, remaining
        else:
            # Empty candidate (e.g. repeated newlines), advance
            buf = buf[end_idx:]
            pos = 0


def split_into_sentences(text: str) -> List[str]:
    """Split complete text into sentence chunks."""
    if not text or not text.strip():
        return []

    sentences = []
    buf = text
    while True:
        sentence, buf = extract_next_sentence(buf, at_eof=False)
        if sentence is None:
            sentence, buf = extract_next_sentence(buf, at_eof=True)
            if sentence is not None:
                sentences.append(sentence)
            break
        sentences.append(sentence)

    return sentences
