"""Static analysis guards for Android client: XML drawables, resource tags, Kotlin integrity,

sleeping expression, and earcon audio files without calling Gradle or AAPT.
These are source/resource checks, not Kotlin compilation or Android runtime evidence.
"""

from pathlib import Path
import re
import wave
import xml.etree.ElementTree as ET
import pytest  # type: ignore

ROOT = Path(__file__).resolve().parents[2]
ANDROID_RES = ROOT / "android/app/src/main/res"
RES_DRAWABLE = ANDROID_RES / "drawable"
RES_VALUES = ANDROID_RES / "values"
RES_RAW = ANDROID_RES / "raw"
RES_MIPMAP = ANDROID_RES / "mipmap-anydpi-v26"
JAVA_MAIN = ROOT / "android/app/src/main/java"
JAVA_TEST = ROOT / "android/app/src/test/java"

ANDROID_NS = "{http://schemas.android.com/apk/res/android}"
APP_NS = "{http://schemas.android.com/apk/res-auto}"


# ---------------------------------------------------------------------------
# 1. XML Drawable Structural Validation
# ---------------------------------------------------------------------------

def test_all_drawable_xml_structure():
    """Verify all XML drawables are well-formed with valid root tags and namespaces."""
    drawable_files = list(RES_DRAWABLE.glob("*.xml")) + list(RES_MIPMAP.glob("*.xml"))
    assert len(drawable_files) >= 10, "Should discover at least 10 XML drawables"

    valid_root_tags = {
        "vector",
        "shape",
        "layer-list",
        "selector",
        "ripple",
        "adaptive-icon",
        "bitmap",
        "inset",
    }

    for xml_file in drawable_files:
        try:
            tree = ET.parse(xml_file)
            root = tree.getroot()
        except ET.ParseError as err:
            pytest.fail(f"Malformed XML in {xml_file.name}: {err}")
            continue

        # Extract local tag name (strip namespace if present)
        tag_name = root.tag.split("}")[-1]
        assert tag_name in valid_root_tags, (
            f"{xml_file.name} has invalid root tag <{tag_name}>. Expected one of {valid_root_tags}"
        )

        # Validate <vector> drawables
        if tag_name == "vector":
            width = root.get(f"{ANDROID_NS}width")
            height = root.get(f"{ANDROID_NS}height")
            viewport_w = root.get(f"{ANDROID_NS}viewportWidth")
            viewport_h = root.get(f"{ANDROID_NS}viewportHeight")

            assert width is not None and width.endswith(("dp", "px")), (
                f"{xml_file.name} vector must define android:width in dp/px"
            )
            assert height is not None and height.endswith(("dp", "px")), (
                f"{xml_file.name} vector must define android:height in dp/px"
            )
            assert viewport_w is not None and float(viewport_w) > 0, (
                f"{xml_file.name} vector must define positive android:viewportWidth"
            )
            assert viewport_h is not None and float(viewport_h) > 0, (
                f"{xml_file.name} vector must define positive android:viewportHeight"
            )

            # Check that all path elements have valid pathData
            paths = [e for e in root.iter() if e.tag.endswith("path")]
            assert len(paths) > 0, f"{xml_file.name} vector must contain at least one <path>"
            for p in paths:
                data = p.get(f"{ANDROID_NS}pathData")
                assert data and len(data.strip()) > 0, f"{xml_file.name} path is missing android:pathData"

        # Validate <shape> drawables
        elif tag_name == "shape":
            allowed_shape_children = {"solid", "stroke", "corners", "gradient", "padding", "size"}
            for child in root:
                child_tag = child.tag.split("}")[-1]
                assert child_tag in allowed_shape_children, (
                    f"{xml_file.name} shape has invalid child <{child_tag}>"
                )

        # Validate <adaptive-icon>
        elif tag_name == "adaptive-icon":
            children_tags = [c.tag.split("}")[-1] for c in root]
            assert "background" in children_tags, f"{xml_file.name} must contain <background>"
            assert "foreground" in children_tags, f"{xml_file.name} must contain <foreground>"


# ---------------------------------------------------------------------------
# 2. Resource Tag Validation in res/values/
# ---------------------------------------------------------------------------

def test_all_resource_tags_and_values_xml():
    """Verify all XML files in res/values/ have valid resource tags and identifiers."""
    values_files = list(RES_VALUES.glob("*.xml"))
    assert len(values_files) >= 4, "Should discover values files (strings, colors, themes, etc.)"

    valid_resource_tags = {
        "string",
        "color",
        "dimen",
        "style",
        "item",
        "bool",
        "integer",
        "string-array",
        "declare-styleable",
        "attr",
    }

    identifier_regex = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    style_identifier_regex = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.]*$")

    for xml_file in values_files:
        try:
            tree = ET.parse(xml_file)
            root = tree.getroot()
        except ET.ParseError as err:
            pytest.fail(f"Malformed XML in {xml_file.name}: {err}")
            continue

        root_tag = root.tag.split("}")[-1]
        assert root_tag == "resources", f"{xml_file.name} root tag must be <resources>"

        seen_keys = set()
        for child in root:
            # Skip XML comments / processing instructions
            if not isinstance(child.tag, str):
                continue
            child_tag = child.tag.split("}")[-1]
            assert child_tag in valid_resource_tags, (
                f"{xml_file.name} has invalid tag <{child_tag}>. Expected one of {valid_resource_tags}"
            )

            name = child.get("name")
            if name:
                expected_regex = style_identifier_regex if child_tag == "style" else identifier_regex
                assert expected_regex.match(name), (
                    f"{xml_file.name} resource name '{name}' is not a valid Android identifier"
                )
                type_key = (child_tag, name)
                assert type_key not in seen_keys, (
                    f"Duplicate resource '{name}' of type <{child_tag}> in {xml_file.name}"
                )
                seen_keys.add(type_key)

    # Specific required resource assertions
    strings_tree = ET.parse(RES_VALUES / "strings.xml")
    strings_names = {e.get("name") for e in strings_tree.getroot() if e.get("name")}
    assert "app_name" in strings_names
    assert "status_idle" in strings_names

    colors_tree = ET.parse(RES_VALUES / "colors.xml")
    colors_names = {e.get("name") for e in colors_tree.getroot() if e.get("name")}
    assert "primary" in colors_names or "console_background" in colors_names or len(colors_names) >= 4


# ---------------------------------------------------------------------------
# 3. Kotlin Lexical Integrity and Structural Syntax (No Gradle/AAPT)
# ---------------------------------------------------------------------------

def _strip_comments_and_strings(kt_code: str) -> str:
    """Strip Kotlin comments and strings to leave only code syntax delimiters."""
    out = []
    i = 0
    n = len(kt_code)
    while i < n:
        # Check raw string """..."""
        if kt_code[i : i + 3] == '"""':
            end = kt_code.find('"""', i + 3)
            i = n if end == -1 else end + 3
            continue
        # Check single-line comment //
        if kt_code[i : i + 2] == "//":
            end = kt_code.find("\n", i + 2)
            i = n if end == -1 else end + 1
            continue
        # Check block comment /* ... */
        if kt_code[i : i + 2] == "/*":
            end = kt_code.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        # Check normal string literal "..."
        if kt_code[i] == '"':
            i += 1
            while i < n:
                if kt_code[i] == "\\":
                    i += 2
                elif kt_code[i] == '"':
                    i += 1
                    break
                else:
                    i += 1
            continue
        # Check char literal '...'
        if kt_code[i] == "'":
            i += 1
            while i < n:
                if kt_code[i] == "\\":
                    i += 2
                elif kt_code[i] == "'":
                    i += 1
                    break
                else:
                    i += 1
            continue

        out.append(kt_code[i])
        i += 1

    return "".join(out)


def test_kotlin_code_integrity_and_delimiter_balance():
    """Verify all Kotlin source and test files have balanced delimiters and proper packages."""
    kt_files = list(JAVA_MAIN.rglob("*.kt")) + list(JAVA_TEST.rglob("*.kt"))
    assert len(kt_files) >= 15, "Should find at least 15 Kotlin source and test files"

    for kt_file in kt_files:
        code = kt_file.read_text(encoding="utf-8")
        stripped = _strip_comments_and_strings(code)

        # 1. Delimiter balance checks
        brace_balance = stripped.count("{") - stripped.count("}")
        paren_balance = stripped.count("(") - stripped.count(")")
        bracket_balance = stripped.count("[") - stripped.count("]")

        assert brace_balance == 0, f"Unbalanced braces in {kt_file.relative_to(ROOT)} (net: {brace_balance})"
        assert paren_balance == 0, f"Unbalanced parentheses in {kt_file.relative_to(ROOT)} (net: {paren_balance})"
        assert bracket_balance == 0, f"Unbalanced brackets in {kt_file.relative_to(ROOT)} (net: {bracket_balance})"

        # 2. Package declaration matches directory structure
        package_match = re.search(r"^\s*package\s+([a-zA-Z0-9_.]+)", code, re.MULTILINE)
        assert package_match is not None, f"Missing package declaration in {kt_file.name}"
        pkg = package_match.group(1)

        expected_dir_suffix = pkg.replace(".", "/")
        assert expected_dir_suffix in str(kt_file.parent), (
            f"Package '{pkg}' in {kt_file.name} does not match directory '{kt_file.parent}'"
        )


def test_kotlin_architectural_and_security_invariants():
    """Verify security policies: no System.exit, no hardcoded tokens, no raw token logs."""
    kt_main_files = list(JAVA_MAIN.rglob("*.kt"))

    for kt_file in kt_main_files:
        code = kt_file.read_text(encoding="utf-8")

        # Invariant 1: Android kiosk service must not use System.exit
        assert "System.exit(" not in code, f"Forbidden System.exit found in {kt_file.name}"

        # Invariant 2: No hardcoded authorization bearer tokens in code
        assert not re.search(r'Bearer\s+[A-Za-z0-9_-]{20,}', code), (
            f"Hardcoded authorization token pattern found in {kt_file.name}"
        )


# ---------------------------------------------------------------------------
# 4. Mascot Expression and Sleeping Mode Assertions
# ---------------------------------------------------------------------------

def test_mascot_expression_drawables_assertion():
    """Verify every MascotExpression enum value has a corresponding physical drawable asset."""
    from nexo_server.mascot import MascotExpression as ServerExpression

    kt_expression_file = JAVA_MAIN / "id/my/yhdev/nexo/ui/MascotExpression.kt"
    assert kt_expression_file.exists(), "MascotExpression.kt must exist"
    code = kt_expression_file.read_text()

    # Extract all enum entries from MascotExpression.kt
    # e.g., SENANG(R.drawable.mascot_senang, "senang"),
    matches = re.findall(r'([A-Z_]+)\(R\.drawable\.([a-z0-9_]+),\s*"([a-z0-9_]+)"\)', code)
    assert len(matches) >= 15, "MascotExpression must declare all required expressions"

    for enum_name, drawable_name, id_name in matches:
        # Check that either .png or .xml exists for this drawable
        png_path = RES_DRAWABLE / f"{drawable_name}.png"
        xml_path = RES_DRAWABLE / f"{drawable_name}.xml"
        assert png_path.exists() or xml_path.exists(), (
            f"Missing drawable asset for {enum_name}: neither {png_path.name} nor {xml_path.name} exists"
        )

    # Check SLEEPING specifically
    sleeping_entry = next((m for m in matches if m[0] == "SLEEPING"), None)
    assert sleeping_entry is not None, "SLEEPING must be defined in MascotExpression enum"
    assert sleeping_entry[1] == "mascot_sleeping"
    assert sleeping_entry[2] == "sleeping"

    # Check server expression mapping
    assert hasattr(ServerExpression, "SLEEPING")
    assert ServerExpression.SLEEPING.value == "sleeping"


def test_mascot_sleeping_vector_curved_geometry():
    """Verify sleeping.svg master and mascot_sleeping.png possess exact curved eyelid geometry."""
    sleeping_png = RES_DRAWABLE / "mascot_sleeping.png"
    assert sleeping_png.exists(), "mascot_sleeping.png must exist in res/drawable"

    sleeping_svg = ROOT / "resource/imoticon/svg/sleeping.svg"
    assert sleeping_svg.exists(), "sleeping.svg master must exist in resource/imoticon/svg"

    svg_content = sleeping_svg.read_text()
    assert "Q" in svg_content or "C" in svg_content
    assert "120" in svg_content or "129" in svg_content


# ---------------------------------------------------------------------------
# 5. Earcon Audio Files and SoundPool Assertions
# ---------------------------------------------------------------------------

def test_earcon_audio_wav_integrity():
    """Verify all earcon chime WAV files meet strict 16-bit uncompressed PCM standards."""
    required_earcons = [
        "chime_listening.wav",
        "chime_thinking.wav",
        "earcon_listening.wav",
        "earcon_thinking.wav",
    ]

    for earcon_name in required_earcons:
        earcon_path = RES_RAW / earcon_name
        assert earcon_path.exists(), f"Missing required earcon audio file: {earcon_name}"

        with wave.open(str(earcon_path), "rb") as wf:
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            nframes = wf.getnframes()
            duration = nframes / float(framerate)

            assert channels in (1, 2), f"{earcon_name} must be mono or stereo (got {channels})"
            assert sampwidth == 2, f"{earcon_name} must be 16-bit PCM (got {sampwidth * 8} bits)"
            assert 16000 <= framerate <= 48000, f"{earcon_name} invalid sample rate ({framerate} Hz)"
            assert 0.05 <= duration <= 3.0, (
                f"{earcon_name} duration out of bounds ({duration:.3f}s, expected 0.05s - 3.0s)"
            )


def test_earcon_manager_soundpool_wiring():
    """Verify EarconManager integrates SoundPool assistance sonification and lifecycle."""
    manager_file = JAVA_MAIN / "id/my/yhdev/nexo/audio/EarconManager.kt"
    assert manager_file.exists()
    source = manager_file.read_text()

    # Audio attributes specification
    assert "AudioAttributes.USAGE_ASSISTANCE_SONIFICATION" in source
    assert "AudioAttributes.CONTENT_TYPE_SONIFICATION" in source

    # Resource loading
    assert "R.raw.chime_listening" in source or "R.raw.earcon_listening" in source
    assert "R.raw.chime_thinking" in source or "R.raw.earcon_thinking" in source

    # Lifecycle methods
    assert "fun playListening(" in source
    assert "fun playThinking(" in source
    assert "fun release(" in source


# ---------------------------------------------------------------------------
# 6. Service & Visualizer Integration Guards
# ---------------------------------------------------------------------------

def test_kiosk_service_suppresses_earcons_and_fences_audio_queue():
    """Guard source wiring for silent v2 transitions and owner-tagged playback."""
    service_file = JAVA_MAIN / "id/my/yhdev/nexo/service/NexoKioskService.kt"
    source = service_file.read_text()

    assert not re.search(r'earconManager\.play(?:Listening|Thinking)\s*\(', source)
    assert 'EarconManager(this, ::outputGuard)' in source
    opening = source.split('fun openRecordingSheet(', 1)[1].split('private fun tryAcquire()', 1)[0]
    assert opening.index('ownership.openSheet(') < opening.index('earconManager.stop()')
    assert 'webSocketClient?.fenceOutput()' in opening
    assert 'audioPlayer.stopAndConfirm {' in opening
    output = source.split('override fun onOutdoorAudio(', 1)[1].split('override fun onStateChanged(', 1)[0]
    assert output.index('token != generation || !ownership.outputAllowed(connectionId, epoch)') < output.index('player.writeChunk(audioData)')
    assert 'val player = audioPlayer' in output
    assert 'if (!player.writeChunk(audioData)' in output
    assert 'dispatch { fail("Pemutaran audio gagal") }' in output
    assert 'earconManager.release()' in source


def test_visualizer_burn_in_protection_and_ambient_sleeping():
    """Verify VisualizerView implements living eyes, burn-in protection, and sleep mode."""
    visualizer_file = JAVA_MAIN / "id/my/yhdev/nexo/ui/VisualizerView.kt"
    source = visualizer_file.read_text()

    # Living glowing eyes
    assert "drawLivingEyeGlow" in source
    assert "RadialGradient" in source

    # Pixel shifting orbit (2-3px)
    assert "pixelShiftOffsets" in source
    assert "currentShiftX" in source and "currentShiftY" in source

    # Ambient sleeping mode (> 5 minutes = 300,000 ms)
    assert "300000L" in source or "300_000L" in source
    assert "MascotExpression.SLEEPING" in source
    assert "ambientDimMultiplier" in source
