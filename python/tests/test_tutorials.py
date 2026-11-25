"""The tutorials point at examples that exist, their no-camera commands run and print what the page shows, and every
page has the same sections.

Commands are taken from the fenced code blocks under a "Without a ..." line of each page (lines starting
`python examples/` or `python -m opticmix_vision.`) and run in order, each in its own process, in one temporary folder
per page — as a reader would. Each must have --synthetic or read no camera, and none may open a window. The page shows
the output of each of them in a ```text block (`$ python ...` and the lines under it); stdout and stderr together must
match it line by line, up to numbers, with `<temporary folder>` standing for any path.
"""

from __future__ import annotations

import os
import re
import shlex
import site
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TUTORIALS = ROOT / "tutorials"
PAGES = sorted(TUTORIALS.glob("[0-9][0-9]-*.md"))
RUN_PAGES = [*PAGES, TUTORIALS / "tools.md"]

SECTIONS = ("What you will do", "Before you start", "Run it", "What you should see", "How it works",
            "If it does not work", "Next", "Rust")    # Rust: the crate's examples for the page's steps, or "no ... yet"
SYNTHETIC_LABELS = ("Output with `--synthetic` (generated frames); with the camera the numbers differ.",
                    "Output with `--synthetic` (a made-up calibration, not a camera).")   # 08: no frames at all
NO_CAMERA = {"play_recording.py", "torch_dataloader.py"}          # never open a camera
WINDOWED = {"quickstart.py", "live_preview.py", "save_stereo_pairs.py", "play_recording.py", "opticmix_vision.viewer"}
# Printed only when generated frames came late, i.e. when this machine was too busy to keep 90 fps: whether it is
# there depends on the load, not on the command (like the numbers, which are not compared either).
LOAD_ONLY = ("Frames were missed while recording.",)


def commands(page: Path) -> list[str]:
    """The `python ...` lines of the blocks under a "Without a ..." line, without their comments."""
    out, fenced, no_camera = [], False, False
    for line in page.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and line.startswith("With"):
            no_camera = line.startswith("Without")       # "With the camera:" / "Without a camera:"
        elif fenced and no_camera and line.startswith(("python examples/", "python -m opticmix_vision.")):
            out.append(line.split("  #")[0].strip())
    return out


def shown(page: Path) -> dict[str, list[str]]:
    """{command: the output lines the page shows for it} from the ```text blocks that start with `$ python`."""
    out = {}
    for block in re.findall(r"```text\n(.*?)```", page.read_text(encoding="utf-8"), re.S):
        if block.startswith("$ python"):
            for chunk in re.split(r"^\$ ", block, flags=re.M)[1:]:
                cmd, *lines = chunk.splitlines()
                out[cmd.strip()] = [line for line in lines if line.strip()]
    return out


def same(shown_line: str, got_line: str) -> bool:
    """Equal once every number is N and backslashes are slashes (pages are written on Windows, tests run on Linux too);
    `<temporary folder>` stands for any path."""
    def norm(s: str) -> str:
        return re.sub(r"\d+(\.\d+)?", "N", s.replace("\\", "/")).rstrip()
    pattern = re.escape(norm(shown_line)).replace(re.escape("<temporary folder>"), ".*")
    return re.fullmatch(pattern, norm(got_line)) is not None


def withheld_examples() -> set[str]:
    """Examples kept out of the public package. An exported tree (the public repository, release checks) has no
    .gitattributes and no withheld files, so nothing to leave out."""
    attrs = ROOT / ".gitattributes"
    if not attrs.is_file():
        return set()
    lines = attrs.read_text(encoding="utf-8").splitlines()
    return {line.split()[0] for line in lines if "export-ignore" in line and line.startswith("examples/")}


def test_eight_pages():
    assert [p.name[:2] for p in PAGES] == [f"{i:02d}" for i in range(1, 9)]


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.stem)
def test_page_has_every_section_in_order(page):
    text = page.read_text(encoding="utf-8")
    heads = [h for h in re.findall(r"^## (.+)$", text, re.M)]
    assert heads == list(SECTIONS), f"{page.name}: {heads}"
    assert any(label in text for label in SYNTHETIC_LABELS), page.name


def test_referenced_examples_exist_and_are_published():
    texts = [p.read_text(encoding="utf-8") for p in TUTORIALS.glob("*.md")]
    names = {m for t in texts for m in re.findall(r"examples/([A-Za-z0-9_]+\.py)", t)}
    assert names, "no example referenced"
    for name in sorted(names):
        assert (ROOT / "examples" / name).is_file(), name
        assert f"examples/{name}" not in withheld_examples(), name


def test_every_published_example_is_in_a_tutorial():
    text = "".join(p.read_text(encoding="utf-8") for p in TUTORIALS.glob("*.md"))
    published = [p.name for p in sorted((ROOT / "examples").glob("*.py"))
                 if f"examples/{p.name}" not in withheld_examples()]
    assert [name for name in published if f"examples/{name}" not in text] == []


def test_shown_and_same():
    assert same("calibration folder: <temporary folder>\\store", r"calibration folder: C:\Temp\tmp1x2\store")
    assert same("wrote 96 frames at 90.0 per second", "wrote 92 frames at 89.7 per second")
    assert not same("wrote 96 frames", "read 96 frames")
    assert shown(PAGES[0]), "01 shows no output"


@pytest.mark.parametrize("page", RUN_PAGES, ids=lambda p: p.stem)
def test_no_camera_commands_run_and_print_what_is_shown(page, tmp_path):
    cmds, outputs = commands(page), shown(page)
    assert cmds, f"{page.name}: no command to run without a camera"
    assert list(outputs) == cmds, f"{page.name}: the commands run without a camera and those with output shown differ"
    appdata = tmp_path / "appdata"     # where the user's calibration folder would be on Windows
    env = {**os.environ, "APPDATA": str(appdata), "PYTHONUSERBASE": site.getuserbase(), "PYTHONUNBUFFERED": "1",
           "PYTHONPATH": os.pathsep.join(filter(None, [str(ROOT), os.environ.get("PYTHONPATH")]))}
    for cmd in cmds:
        args = shlex.split(cmd)[1:]
        module = args[0] == "-m"
        name = args[1] if module else Path(args[0]).name
        assert "--synthetic" in args or "--recording" in args or name in NO_CAMERA, f"{page.name}: {cmd} opens a camera"
        if name in WINDOWED:
            assert "--headless" in args, f"{page.name}: {cmd} would open a window"
        if name == "torch_dataloader.py":
            pytest.importorskip("torch")
        argv = [sys.executable, *args] if module else [sys.executable, str(ROOT / args[0]), *args[1:]]
        r = subprocess.run(argv, cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
        assert r.returncode == 0, f"{page.name}: {cmd} exited {r.returncode}\n{r.stdout}"
        got, want = ([line for line in lines if line.strip() and not line.startswith(LOAD_ONLY)]
                     for lines in (r.stdout.splitlines(), outputs[cmd]))
        assert len(got) == len(want) and all(map(same, want, got)), \
            f"{page.name}: {cmd}\n-- shown:\n" + "\n".join(want) + "\n-- printed:\n" + "\n".join(got)
    assert not appdata.exists()
