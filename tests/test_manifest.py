"""Structural checks of the provider folder, mirroring the community catalog rules.

These tests run without the Music Assistant server: they only validate the
files the catalog checks before it even loads the provider.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from tests import DOMAIN

REPOSITORY = Path(__file__).parents[1]
PROVIDER_FOLDER = REPOSITORY / DOMAIN
SVG_NS = "http://www.w3.org/2000/svg"
MAX_ICON_BYTES = 5 * 1024
MAX_MANIFEST_SIZE = 16 * 1024
PROVIDER_TYPES = {"music", "player", "metadata", "plugin", "audio_analysis"}
STAGES = {"alpha", "beta", "stable", "experimental", "unmaintained", "deprecated"}
OFFERINGS = {
    "artist", "album", "track", "playlist", "radio", "podcast", "audiobook",
    "sound_effect", "audio_source", "ai", "tts", "scrobble", "assistant",
    "visualizer", "recommendations", "fun",
}
DOMAIN_RE = re.compile(r"[a-z][a-z0-9_]*")
REQUIREMENT_RE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9._, -]+\])?"
    r"(\s*(==|>=|<=|~=|!=|<|>)\s*[A-Za-z0-9.*+!_-]+"
    r"(\s*,\s*(==|>=|<=|~=|!=|<|>)\s*[A-Za-z0-9.*+!_-]+)*)?"
)
GITHUB_HANDLE_RE = re.compile(r"[A-Za-z0-9](?:-?[A-Za-z0-9])*")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_folder_is_named_after_the_domain() -> None:
    """The catalog requires the provider folder to be named after its domain."""
    assert PROVIDER_FOLDER.is_dir()
    assert PROVIDER_FOLDER.name == DOMAIN


def test_manifest_exists_and_is_plain_json() -> None:
    """The manifest must be a plain JSON object below the size limit."""
    manifest = _load(PROVIDER_FOLDER / "manifest.json")
    assert isinstance(manifest, dict)
    size = len(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode())
    assert size <= MAX_MANIFEST_SIZE


def test_manifest_core_fields() -> None:
    """Type, domain, name, description, codeowners, stage and provides."""
    manifest = _load(PROVIDER_FOLDER / "manifest.json")
    assert manifest["type"] in PROVIDER_TYPES
    assert manifest["domain"] == DOMAIN
    assert DOMAIN_RE.fullmatch(manifest["domain"])
    assert isinstance(manifest["name"], str) and manifest["name"].strip()
    assert isinstance(manifest["description"], str) and manifest["description"].strip()
    assert isinstance(manifest["stage"], str) and manifest["stage"] in STAGES
    assert isinstance(manifest["codeowners"], list) and manifest["codeowners"]
    for owner in manifest["codeowners"]:
        assert owner.startswith("@")
        assert GITHUB_HANDLE_RE.fullmatch(owner[1:])
    assert isinstance(manifest["provides"], list)
    assert set(manifest["provides"]) <= OFFERINGS
    assert manifest.get("multi_instance") in (True, False)


def test_manifest_requirements_format() -> None:
    """Requirements must be plain package names with version specifiers."""
    manifest = _load(PROVIDER_FOLDER / "manifest.json")
    requirements = manifest.get("requirements", [])
    assert isinstance(requirements, list)
    assert len(requirements) <= 50
    for requirement in requirements:
        assert isinstance(requirement, str)
        assert requirement.isprintable()
        assert REQUIREMENT_RE.fullmatch(requirement), requirement


def test_documentation_is_an_https_url() -> None:
    """The documentation field should point to an https address."""
    manifest = _load(PROVIDER_FOLDER / "manifest.json")
    documentation = manifest.get("documentation")
    assert documentation is not None
    assert documentation.startswith("https://")


def test_strings_json_covers_manifest_and_config_entries() -> None:
    """English texts: manifest name/description and the labels of the settings."""
    manifest = _load(PROVIDER_FOLDER / "manifest.json")
    strings = _load(PROVIDER_FOLDER / "strings.json")
    assert strings["manifest"]["name"].strip()
    assert strings["manifest"]["description"].strip()
    # the manifest description and the English text agree (the manifest text is
    # what a server shows when no translation is loaded)
    assert strings["manifest"]["name"] == manifest["name"]
    assert strings["manifest"]["description"] == manifest["description"]
    config_entries = strings.get("config_entries", {})
    for key in ("bridge_host", "bridge_port", "api_token"):
        assert key in config_entries, f"strings.json misses config_entries.{key}"
        assert config_entries[key]["label"].strip()


def test_translations_are_valid_and_complete() -> None:
    """Every translation file is valid JSON with the same structure."""
    strings = _load(PROVIDER_FOLDER / "strings.json")
    translations = PROVIDER_FOLDER / "translations"
    assert translations.is_dir()
    for file in sorted(translations.glob("*.json")):
        data = _load(file)
        assert "manifest" in data, file.name
        assert data["manifest"]["name"].strip()
        assert data["manifest"]["description"].strip()
        for key, texts in strings.get("config_entries", {}).items():
            entry = data.get("config_entries", {}).get(key)
            assert entry is not None, f"{file.name} misses config_entries.{key}"
            assert entry.get("label", "").strip(), f"{file.name}: {key}.label is empty"
            assert set(entry) <= set(texts) | {"label", "description"}, file.name


def test_icon_is_a_safe_small_svg() -> None:
    """The icon draws only, stays small and is a valid SVG document."""
    icon = PROVIDER_FOLDER / "icon.svg"
    assert icon.is_file()
    svg = icon.read_text(encoding="utf-8")
    assert len(svg.encode()) <= MAX_ICON_BYTES
    root = ET.fromstring(svg)
    assert root.tag == f"{{{SVG_NS}}}svg"
    allowed = {
        "circle", "clipPath", "defs", "desc", "ellipse", "g", "line",
        "linearGradient", "marker", "mask", "path", "pattern", "polygon",
        "polyline", "radialGradient", "rect", "stop", "style", "svg", "symbol",
        "text", "textPath", "title", "tspan", "image", "use", "filter",
    }
    for element in root.iter():
        tag = element.tag.split("}")[-1]
        assert tag in allowed, tag
        for key, value in element.attrib.items():
            assert not key.lower().startswith("on"), key
            if key == "href":
                assert value.startswith("#"), value


def test_no_python_files_compile() -> None:
    """Every Python file of the provider compiles on its own."""
    import py_compile

    for file in PROVIDER_FOLDER.rglob("*.py"):
        py_compile.compile(str(file), doraise=True)
