"""Deterministic, bounded Indonesian mascot routing; not an emotion model.

Only full standalone grammar matches are local commands. Unknown wording,
questions, quotes, negations (except the literal ``jangan sedih`` idiom), and
mixed tasks fall through to the agent. No input is interpolated into replies.
"""

from enum import Enum
import re
from typing import Optional


class MascotExpression(str, Enum):
    NETRAL = "netral"
    SENANG = "senang"
    SANGAT_SENANG = "sangat_senang"
    TERTAWA = "tertawa"
    BINGUNG = "bingung"
    MENGANTUK = "mengantuk"
    MENGEDIP = "mengedip"
    TERKEJUT = "terkejut"
    CINTA = "cinta"
    JAHIL = "jahil"
    KEREN = "keren"
    MALU = "malu"
    MARAH = "marah"
    MENANGIS = "menangis"
    SEDIH = "sedih"
    TAKUT = "takut"
    SLEEPING = "sleeping"


# Filler may occur before/after one wakeword; the entire string must match.
_FILLER = r"(?:tolong|mohon|coba)\s+"
_COMMAND = re.compile(
    rf"(?:{_FILLER})?(?:(?:nexo|nexo)[,]?\s+)?(?:{_FILLER})?"
    r"(?P<body>.+?)(?:\s+(?:dong|ya))?[.!]?"
)
_TARGET = re.compile(
    r"(?:(?:tunjukkan|tampilkan)\s+(?:wajah|ekspresi)\s+"
    r"|(?:wajah|ekspresi)\s+"
    r"|ubah\s+(?:wajah|ekspresi)\s+(?:menjadi|ke)\s+)"
    r"(?P<label>[a-z_]+(?:\s+[a-z_]+)?)"
)
_LABEL_ALIASES = {
    "normal": "netral",
    "bahagia": "senang",
    "gembira": "senang",
    "senyum": "senang",
    "lucu": "tertawa",
    "ngantuk": "mengantuk",
    "kaget": "terkejut",
    "seti": "sedih",
    "optimis": "sangat_senang",
    "tidur": "sleeping",
    "sleep": "sleeping",
}
_ACTIONS = {
    "senyum": MascotExpression.SENANG,
    "tersenyum": MascotExpression.SENANG,
    "tertawa": MascotExpression.TERTAWA,
    "menangis": MascotExpression.MENANGIS,
    "mengedip": MascotExpression.MENGEDIP,
    "berkedip": MascotExpression.MENGEDIP,
    "kembali normal": MascotExpression.NETRAL,
    "jangan sedih": MascotExpression.SENANG,
    "tidur": MascotExpression.SLEEPING,
}

EMOTIONAL_TAG_PATTERN = re.compile(r"\[expr:\s*([a-zA-Z0-9_\s-]+?)\s*\]", re.IGNORECASE)


def parse_and_strip_emotional_tags(text: str) -> tuple[Optional[MascotExpression], str]:
    """Parse explicit emotional tags [expr:...] from text and strip them.

    Returns:
        (expression, clean_text): The first recognized MascotExpression (or None),
        and the text with all [expr:...] tags removed and whitespace normalized.
    """
    if not text:
        return None, ""

    first_expression: Optional[MascotExpression] = None

    def _replace_tag(match: re.Match) -> str:
        nonlocal first_expression
        raw_label = match.group(1).strip().lower().replace("-", "_").replace(" ", "_")
        target_label = _LABEL_ALIASES.get(raw_label, raw_label)
        try:
            expr = MascotExpression(target_label)
            if first_expression is None:
                first_expression = expr
        except ValueError:
            pass
        return ""

    cleaned = EMOTIONAL_TAG_PATTERN.sub(_replace_tag, text)
    # Clean up whitespace: normalize intra-line spaces and remove spacing before punctuation
    lines = []
    for line in cleaned.splitlines():
        line_clean = re.sub(r"[ \t]+", " ", line).strip()
        line_clean = re.sub(r"\s+([.,!?;:])", r"\1", line_clean)
        lines.append(line_clean)

    result_text = "\n".join(l for l in lines if l).strip()
    return first_expression, result_text


def parse_emotional_tag(text: str) -> Optional[MascotExpression]:
    """Extract the first recognized emotional tag [expr:...] from text."""
    expr, _ = parse_and_strip_emotional_tags(text)
    return expr


def strip_emotional_tags(text: str) -> str:
    """Remove all emotional tags [expr:...] from text."""
    _, clean = parse_and_strip_emotional_tags(text)
    return clean


def parse_expression_command(text: str) -> MascotExpression | None:
    """Return an allowed expression only for a complete supported command."""
    match = _COMMAND.fullmatch(" ".join(text.casefold().split()))
    if not match:
        return None
    body = match["body"]
    if body in _ACTIONS:
        return _ACTIONS[body]
    target = _TARGET.fullmatch(body)
    if target:
        try:
            label = target["label"].replace(" ", "_")
            return MascotExpression(_LABEL_ALIASES.get(label, label))
        except ValueError:
            pass
    return None


def _has_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    """Word/phrase matches, ignoring locally negated occurrences."""
    for phrase in phrases:
        for match in re.finditer(r"\b" + re.escape(phrase) + r"\b", text):
            # Local negation only, not sentence semantics.
            prefix = text[:match.start()].split()[-3:]
            if not set(prefix) & {"tidak", "bukan", "jangan", "belum", "tak"}:
                return True
    return False


def contextual_expression(user_text: str, reply_text: str) -> MascotExpression:
    """Conservative phrase heuristics; sadness/empathy outranks upbeat cues.

    Explicit commands bypass these heuristics. Generic emotion words in
    explanations are deliberately not enough. This is not emotion understanding.
    """
    user = " ".join(user_text.casefold().split())
    reply = " ".join(reply_text.casefold().split())
    if _has_phrase(user, (
        "aku sedih", "saya sedih", "aku merasa sedih", "saya merasa sedih",
        "aku sedang sedih", "saya sedang sedih", "aku berduka", "saya berduka",
        "aku sedang berduka", "saya sedang berduka", "aku kehilangan",
    )) or _has_phrase(reply, ("turut berduka", "turut prihatin", "aku ikut sedih", "saya ikut sedih")):
        return MascotExpression.SEDIH
    if _has_phrase(reply, ("tidak tahu", "belum yakin", "kurang yakin", "belum pasti", "perlu klarifikasi")):
        return MascotExpression.BINGUNG
    if _has_phrase(reply, ("haha", "hahaha", "wkwk")) or _has_phrase(user, (
        "ceritakan lelucon", "ceritakan humor", "buat lelucon",
    )):
        return MascotExpression.TERTAWA
    if _has_phrase(reply, ("berhasil", "selamat atas", "luar biasa", "hebat")):
        return MascotExpression.SANGAT_SENANG
    greetings = ("halo", "hai", "selamat pagi", "selamat siang", "selamat sore", "selamat malam")
    if _has_phrase(user, greetings) or _has_phrase(reply, greetings):
        return MascotExpression.SENANG
    return MascotExpression.NETRAL
