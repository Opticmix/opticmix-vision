"""API.md must match the code: every public name is documented, and every documented signature is the real one.

Format API.md follows (and this test parses):
  ## `opticmix_vision.<module>`                   starts a module section
  ### `name(params) -> ret`  /  #### `Class.method(params) -> ret`
                                                 a signature, compared with inspect.signature
  ### `name`  /  #### `Class.attr`               a name without a signature: it must exist
"""
from __future__ import annotations

import importlib
import inspect
import re
from pathlib import Path

import pytest

import opticmix_vision

DOC = Path(__file__).resolve().parents[1] / "API.md"
MODULES = ["device", "devices", "controls", "led", "synthetic", "recording", "calibration", "calib", "presets", "doctor", "viewer", "explorer"]

MODULE_RE = re.compile(r"^## `(opticmix_vision(?:\.\w+)*)`\s*$")
ENTRY_RE = re.compile(r"^#{3,4} `([A-Za-z_][\w.]*)(\(.*\)(?: -> .+)?)?`\s*$")


def _sections() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    cur = None
    for line in DOC.read_text(encoding="utf-8").splitlines():
        m = MODULE_RE.match(line)
        if m:
            cur = m.group(1)
            out[cur] = []
        elif cur is not None:
            out[cur].append(line)
    return out


SECTIONS = _sections()
ENTRIES = [(mod, m.group(1), m.group(2)) for mod, lines in SECTIONS.items()
           for m in map(ENTRY_RE.match, lines) if m]


def _norm(text: str) -> str:
    text = re.sub(r"<object object at 0x[0-9A-Fa-f]+>", "<unset>", text)   # LedControl's 'not given' sentinel
    text = text.replace("'", "").replace('"', "")
    text = re.sub(r"ForwardRef\(([^()]*)\)", r"\1", text)   # Python 3.10/3.11 print NamedTuple annotations this way
    return " ".join(text.split())


def _actual(obj) -> str:
    sig = inspect.signature(obj)
    params = [p for p in sig.parameters.values() if not p.name.startswith("_") and p.name != "self"]
    sig = sig.replace(parameters=params)
    if inspect.isclass(obj):                     # the constructor's "-> None" says nothing
        sig = sig.replace(return_annotation=inspect.Signature.empty)
    return _norm(str(sig))


def _resolve(module: str, dotted: str):
    obj = importlib.import_module(module)
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


def test_doc_has_every_public_module():
    assert set(SECTIONS) == {"opticmix_vision"} | {f"opticmix_vision.{m}" for m in MODULES}


def test_package_exports_are_documented():
    text = "\n".join(SECTIONS["opticmix_vision"])
    missing = [n for n in opticmix_vision.__all__ if f"`{n}`" not in text]
    assert not missing, f"names in opticmix_vision.__all__ missing from API.md: {missing}"


@pytest.mark.parametrize("name", MODULES)
def test_module_all_is_documented(name):
    mod = importlib.import_module(f"opticmix_vision.{name}")
    text = "\n".join(SECTIONS[f"opticmix_vision.{name}"])
    missing = [n for n in mod.__all__ if f"`{n}" not in text]
    assert not missing, f"opticmix_vision.{name}.__all__ names missing from API.md: {missing}"


@pytest.mark.parametrize("name", MODULES)
def test_every_public_callable_has_a_signature(name):
    mod = importlib.import_module(f"opticmix_vision.{name}")
    with_sig = {n for m, n, s in ENTRIES if m == mod.__name__ and s}
    need = [n for n in mod.__all__
            if inspect.isfunction(getattr(mod, n))
            or (inspect.isclass(getattr(mod, n)) and not issubclass(getattr(mod, n), BaseException)
                and not getattr(getattr(mod, n), "_is_protocol", False))]
    missing = [n for n in need if n not in with_sig]
    assert not missing, f"no signature heading in API.md for opticmix_vision.{name}: {missing}"


def test_entries_were_parsed():
    assert sum(1 for _, _, s in ENTRIES if s) > 50


@pytest.mark.parametrize("module,dotted,sig", ENTRIES, ids=[f"{m}:{n}" for m, n, _ in ENTRIES])
def test_documented_entry_matches_code(module, dotted, sig):
    obj = _resolve(module, dotted)            # AttributeError: the doc names something that does not exist
    if sig:
        assert _norm(sig) == _actual(obj), f"API.md signature of {module}.{dotted} is stale"
