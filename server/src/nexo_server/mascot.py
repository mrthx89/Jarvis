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
_FILLER = r"(?:tolong|mohon|coba|silakan|ayo)\s+"
_COMMAND = re.compile(
    rf"(?:{_FILLER})?(?:(?:nexo|jarvis)[,]?\s+)?(?:{_FILLER})?"
    r"(?P<body>.+?)(?:\s+(?:dong|ya|dulu))?[.!]?"
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
    "bangun": "netral",
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
    "bangun": MascotExpression.NETRAL,
}

_SLEEP_COMMANDS = {
    "tidur",
    "tidur dulu",
    "masuk mode tidur",
    "mode tidur",
    "waktunya tidur",
    "istirahat",
    "istirahat dulu",
    "selamat tidur",
    "bobo",
    "bobo dulu",
    "sleep",
    "go to sleep",
}

_WAKE_COMMANDS = {
    "bangun",
    "ayo bangun",
    "bangun dong",
    "bangun ya",
    "bangun tidur",
    "bangun nexo",
    "bangun jarvis",
    "mode bangun",
    "masuk mode bangun",
    "wake up",
    "wake",
    "halo nexo",
    "hai nexo",
    "halo jarvis",
    "hai jarvis",
}


def is_sleep_command(text: str) -> bool:
    """Return True if text is a standalone command to enter sleep mode."""
    if not text:
        return False
    clean = " ".join(text.casefold().strip().split())
    if clean in _SLEEP_COMMANDS:
        return True
    match = _COMMAND.fullmatch(clean)
    if match and match["body"].strip() in _SLEEP_COMMANDS:
        return True
    return False


def is_wake_command(text: str) -> bool:
    """Return True if text is a standalone command to wake from sleep mode."""
    if not text:
        return False
    clean = " ".join(text.casefold().strip().split())
    if clean in _WAKE_COMMANDS:
        return True
    match = _COMMAND.fullmatch(clean)
    if match and match["body"].strip() in _WAKE_COMMANDS:
        return True
    return False


_BRIEFING_COMMANDS = {
    "briefing",
    "briefing hari ini",
    "morning briefing",
    "briefing pagi",
    "nexo briefing",
    "jadwal hari ini",
    "agenda hari ini",
}

_PANTUN_COMMANDS = {
    "pantun",
    "pantun nexo",
    "pantun hari ini",
    "pantun pagi",
    "bacakan pantun",
    "minta pantun",
}

_GREETING_COMMANDS = {
    "halo nexo",
    "hai nexo",
    "halo jarvis",
    "hai jarvis",
    "selamat pagi",
    "selamat pagi nexo",
    "selamat siang",
    "selamat siang nexo",
    "selamat sore",
    "selamat sore nexo",
    "selamat malam",
    "selamat malam nexo",
}


def parse_smart_briefing_command(text: str, is_sleeping: bool = False) -> Optional[tuple[str, MascotExpression]]:
    """Return a contextual smart greeting, daily briefing, or wake response."""
    if not text:
        return None
    clean = " ".join(text.casefold().strip().split())
    clean_body = clean
    match = _COMMAND.fullmatch(clean)
    if match:
        clean_body = match["body"].strip()

    if is_sleeping:
        if clean in _WAKE_COMMANDS or clean_body in _WAKE_COMMANDS:
            return ("Halo, saya sudah bangun! Ada yang bisa dibantu?", MascotExpression.SENANG)
        return None

    if clean in _PANTUN_COMMANDS or clean_body in _PANTUN_COMMANDS:
        return (
            "Aroma kopi harum memikat, diseduh hangat di atas meja. "
            "Awali hari dengan semangat, berkarya dengan hati dalam bekerja! "
            "Ada tugas yang ingin kita selesaikan hari ini, Pak Yanto?",
            MascotExpression.SENANG,
        )

    if clean in _BRIEFING_COMMANDS or clean_body in _BRIEFING_COMMANDS:
        from datetime import datetime, timezone, timedelta
        wib = timezone(timedelta(hours=7))
        now_wib = datetime.now(wib)
        days = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
        months = [
            "Januari", "Februari", "Maret", "April", "Mei", "Juni",
            "Juli", "Agustus", "September", "Oktober", "November", "Desember"
        ]
        day_str = days[now_wib.weekday()]
        date_str = f"{now_wib.day} {months[now_wib.month - 1]} {now_wib.year}"
        time_str = now_wib.strftime("%H:%M")
        return (
            f"Selamat pagi Pak Yanto! Hari ini {day_str}, {date_str}, pukul {time_str} WIB. "
            "Sistem Nexo Dedicated Appliance beroperasi prima dan terhubung ke Hermes Agent. "
            "Mari awali hari dengan penuh semangat dan berkarya dengan hati @ 2026! Apa agenda kita hari ini?",
            MascotExpression.SANGAT_SENANG,
        )

    if clean in _GREETING_COMMANDS or clean_body in _GREETING_COMMANDS:
        from datetime import datetime, timezone, timedelta
        wib = timezone(timedelta(hours=7))
        now_wib = datetime.now(wib)
        hour = now_wib.hour
        if 4 <= hour < 11:
            greet = "Selamat pagi Pak Yanto! Semangat berkarya dengan hati hari ini. Ada yang bisa Nexo bantu?"
        elif 11 <= hour < 15:
            greet = "Selamat siang Pak Yanto! Tetap semangat berkarya. Ada yang perlu Nexo siapkan untuk pekerjaan Anda?"
        elif 15 <= hour < 18:
            greet = "Selamat sore Pak Yanto! Bagaimana progres karya hari ini? Nexo siap membantu."
        else:
            greet = "Selamat malam Pak Yanto! Semoga hari ini produktif dan berkah. Ada yang bisa Nexo bantu sebelum istirahat?"
        return (greet, MascotExpression.SENANG)

    if clean in ("bangun", "bangun nexo", "bangun jarvis", "wake up") or clean_body in ("bangun", "bangun nexo", "bangun jarvis", "wake up"):
        return ("Saya sudah aktif dan siap menyimak, Pak Yanto! Ada yang bisa dibantu?", MascotExpression.SENANG)

    return None

EMOTIONAL_TAG_PATTERN = re.compile(r"\[expr:\s*([a-zA-Z0-9_\s-]+?)\s*\]", re.IGNORECASE)
EMOJI_PATTERN = re.compile(r"[\U00010000-\U0010FFFF\u2600-\u27BF\u2300-\u23FF]", flags=re.UNICODE)


def parse_and_strip_emotional_tags(text: str) -> tuple[Optional[MascotExpression], str]:
    """Parse explicit emotional tags [expr:...] from text, strip them and emojis.

    Returns:
        (expression, clean_text): The first recognized MascotExpression (or None),
        and the text with all [expr:...] tags and stray emojis removed.
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
    cleaned = EMOJI_PATTERN.sub("", cleaned)
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
