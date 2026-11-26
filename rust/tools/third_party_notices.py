"""Write python/THIRD_PARTY_NOTICES.txt — the Rust crates statically linked into opticmix_vision._core.

    python rust/tools/third_party_notices.py           # (re)write the file
    python rust/tools/third_party_notices.py --check   # exit 1 if the file is out of date

Takes the crate set from `cargo tree -p opticmix-vision-py` for each wheel's target (Windows and Linux) and feature —
the features the wheel build really resolves (opticmix-vision with default-features = false), not the
workspace-wide union `cargo metadata` reports — so dev-dependencies and crates behind features the
wheel does not turn on never enter the file. Normal edges without proc-macro crates are linked; build
edges and proc-macro crates are listed as build-only. One file serves both wheels: a crate in one
target's build only says which. License files come from `cargo metadata`, as published with each crate. Python standard library and cargo only.
Fails if any crate in the build carries a license outside ALLOWED — check that case by hand.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

RUST = Path(__file__).resolve().parents[1]
OUT = RUST.parent / "python" / "THIRD_PARTY_NOTICES.txt"
ROOT, FEATURE, TARGET = "opticmix-vision-py", "extension-module", "x86_64-pc-windows-msvc"
ALLOWED = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "Zlib", "Unlicense", "BSL-1.0",
           "Unicode-3.0", "Unicode-DFS-2016", "LLVM-exception"}
# The wheels' targets (one notices file for both). TARGET above stays the default of tree() for other callers.
WHEELS = {"x86_64-pc-windows-msvc": "Windows", "x86_64-unknown-linux-gnu": "Linux"}
LICENSE_FILE = re.compile(r"(?i)^(licen[cs]e|copying|notice|copyright)")
RULE = "=" * 78


def cargo(*args):
    return subprocess.run(["cargo", *args, "--offline", "--locked"], cwd=RUST, capture_output=True,
                          check=True, text=True, encoding="utf-8").stdout


def tree(edges, target=None):
    """(name, version) of every crate `cargo tree` shows for the wheel build over these edge kinds."""
    out = cargo("tree", "-p", ROOT, "--target", target or TARGET, "-F", FEATURE, "-e", edges,
                "--prefix", "none", "--format", "{p}")
    return {(ln.split()[0], ln.split()[1].lstrip("v")) for ln in out.splitlines() if ln.strip()}


def render(m, linked_nv, used_nv, only=None):
    """only: (name, version) -> the one wheel ("Windows" / "Linux") whose build has that crate; None = one target."""
    pk = {p["id"]: p for p in m["packages"]}
    by_nv = {(p["name"], p["version"]): i for i, p in pk.items()}
    root = by_nv[next(nv for nv in used_nv if nv[0] == ROOT)]
    linked, used = {by_nv[nv] for nv in linked_nv}, {by_nv[nv] for nv in used_nv}
    bad = [f'{pk[i]["name"]} {pk[i]["version"]}: {pk[i]["license"]}' for i in used
           if not set(re.findall(r"[\w.+-]+", pk[i]["license"] or "?")) - {"OR", "AND", "WITH"} <= ALLOWED]
    if bad:
        sys.exit("license outside the allow-list — not written:\n  " + "\n  ".join(bad))
    key = lambda i: (pk[i]["name"], pk[i]["version"])
    third = sorted((i for i in linked if pk[i]["source"] is not None), key=key)
    other = sorted((i for i in used - linked if pk[i]["source"] is not None), key=key)

    tag = lambda i: f" ({only[key(i)]} wheel only)" if only and key(i) in only else ""
    if only is None:
        what = ["The extension opticmix_vision/_core.pyd is compiled from the Rust crates in rust/",
                f"(Apache-2.0, this repository) and statically links the {len(third)} crates below — the",
                f"dependencies of {ROOT} built for {TARGET} with the wheel's feature",
                "extension-module, at the versions in rust/Cargo.lock. Each crate's license files follow,"]
    else:
        what = ["The extension (opticmix_vision/_core.pyd in the Windows wheel, opticmix_vision/_core.abi3.so",
                "in the Linux wheel) is compiled from the Rust crates in rust/ (Apache-2.0, this repository)",
                f"and statically links the {len(third)} crates below — the dependencies of {ROOT}",
                f"built for {' and '.join(WHEELS)} with the wheel's",
                "feature extension-module, at the versions in rust/Cargo.lock; a crate in one of the two",
                "builds only is marked with that wheel. Each crate's license files follow,"]
    lines = [f"Third-party software in opticmix-vision {pk[root]['version']}", RULE, "", *what,
             "as published with the crate. Where a crate is offered under a choice of licenses, both",
             "texts are reproduced.", "",
             "Rust standard library — MIT OR Apache-2.0",
             "  https://github.com/rust-lang/rust",
             "  Copyright (c) The Rust Project Developers",
             "  Compiled into the extension by rustc (std, core, alloc). Its licenses are the MIT and",
             "  Apache-2.0 texts reproduced at the end of this file. The standard library's complete",
             "  notices, including those of the components it bundles, are in COPYRIGHT-library.html,",
             "  shipped with every Rust toolchain (share/doc/rust/) and in the rust-lang/rust repository.", "",
             "Generated by rust/tools/third_party_notices.py — do not edit by hand.", "", "",
             "Crates linked into the extension", "-" * 32, ""]
    texts = {}                                    # license text -> (file name, [crates])
    for i in third:
        p = pk[i]
        d = Path(p["manifest_path"]).parent
        files = sorted(f for f in d.iterdir() if f.is_file() and LICENSE_FILE.match(f.name))
        if not files:
            sys.exit(f"{p['name']} {p['version']}: no license file in the crate — add its text by hand")
        notices = []
        for f in files:
            t = f.read_text(encoding="utf-8").replace("\r\n", "\n").strip("\n")
            texts.setdefault(t, (f.name, []))[1].append(f"{p['name']} {p['version']}")
            notices += [ln.strip() for ln in t.splitlines()
                        if re.match(r"\s*(Copyright\b(?! notice| license| owner)|©)", ln) and "[yyyy]" not in ln]
        if not notices:
            notices = ["Authors (Cargo.toml): " + ", ".join(re.sub(r"\s*<[^>]*>", "", a) for a in p["authors"])
                       if p["authors"] else "No copyright line in the crate's license files."]
        lines += [f"{p['name']} {p['version']} — {p['license']}{tag(i)}",
                  f"  {p.get('repository') or 'https://crates.io/crates/' + p['name']}"]
        lines += [f"  {n}" for n in dict.fromkeys(notices)] + [""]
    lines += ["", "Used only to build, not contained in the extension", "-" * 50, ""]
    lines += [f"{pk[i]['name']} {pk[i]['version']} — {pk[i]['license']}{tag(i)}" for i in other]
    for t, (name, users) in texts.items():
        lines += ["", "", RULE, f"{name} — {', '.join(users)}", RULE, "", t]
    return "\n".join(lines) + "\n"


def main():
    meta = json.loads(cargo("metadata", "--format-version", "1", *(x for t in WHEELS for x in ("--filter-platform", t))))
    sets = {t: (tree("normal,no-proc-macro", t), tree("normal,build", t)) for t in WHEELS}
    linked, used = (set().union(*(s[k] for s in sets.values())) for k in (0, 1))
    only = {nv: name for nv in used for t, name in WHEELS.items()
            if all((nv in sets[u][1]) == (u == t) for u in WHEELS)}
    text = render(meta, linked, used, only)
    if "--check" in sys.argv:
        ok = OUT.is_file() and OUT.read_text(encoding="utf-8") == text
        print(f"{OUT.name}: {'up to date' if ok else 'OUT OF DATE - run without --check'}")
        sys.exit(0 if ok else 1)
    OUT.write_bytes(text.encode("utf-8"))
    print(f"wrote {OUT} ({text.count(chr(10))} lines)")


if __name__ == "__main__":
    main()
