# opticmix-vision

Python SDK for the **Weevia S1** stereo infrared camera. The camera presents
itself to the host as a standard UVC device; the SDK opens it, splits the two
eyes out of one frame, and drives the camera controls and the IR illuminator.
Camera controls and the illuminator: measured with the earlier Python implementation; the Rust implementation in 0.0.3 has not yet been verified against the camera.

## Install

Windows x64, Python 3.10 or newer:

```bash
pip install opticmix-vision
```

This installs a wheel that carries a compiled extension, so nothing has to be
built. The same file is attached to the GitHub release
[v0.0.3.post1](https://github.com/Opticmix/opticmix-vision/releases/tag/v0.0.3.post1).
On Windows, verified on Windows 11 only. There is no wheel for macOS, and macOS
has not been run.

**Linux x86_64 — experimental, not a supported platform.** The 0.0.3 release on
GitHub also carries `opticmix_vision-0.0.3-cp310-abi3-linux_x86_64.whl` (not on
PyPI, where Linux installs from source and needs a Rust toolchain). It has been run
on one Ubuntu 26.04 machine with one camera; camera controls on Linux have not
been verified and are not supported. See [python/README.md](python/README.md#linux-experimental).

To build from the repository instead you need a Rust toolchain, 1.88 or newer (the package has been built and tested with 1.88 and 1.99), and on
Windows the Microsoft C++ Build Tools, which Rust's default MSVC toolchain links with:

```bash
pip install "git+https://github.com/Opticmix/opticmix-vision#subdirectory=python"
```

## What is here

| Folder | What it holds |
|---|---|
| [`python/`](python/) | the `opticmix_vision` package, its tests and an example. Start with [python/README.md](python/README.md) |
| [`rust/`](rust/) | the core behind camera controls and device listing, and the extension that exposes it to Python as `opticmix_vision._core` |
| [`ros2/`](ros2/) | weevia_ros — experimental ROS 2 package, not a supported feature; see [ros2/README.md](ros2/README.md) |

What changed between releases: [python/CHANGELOG.md](python/CHANGELOG.md).

## Upgrading from 0.0.2

Install the 0.0.3 wheel over 0.0.2 —
`pip install --upgrade opticmix_vision-0.0.3-cp310-abi3-win_amd64.whl` — with no
uninstall first. Every name 0.0.2 exported from the package is still there with
the same arguments; 0.0.3 only adds optional ones. `set_prop()` and `get_prop()`
behave differently in the cases listed under *Changed* of [python/CHANGELOG.md](python/CHANGELOG.md),
and the viewer dropped options that did nothing in the package (*Removed*). Installing from the
repository instead of the wheel: the package is now in `python/`, so add
`#subdirectory=python` to the URL, and the build needs a Rust toolchain, 1.88 or newer (the package has been built and tested with 1.88 and 1.99), and on
Windows the Microsoft C++ Build Tools, which Rust's default MSVC toolchain links with. New in the distribution: `THIRD_PARTY_NOTICES.txt`, the licenses of the
Rust crates compiled into the extension.

## Getting help

Open an issue at https://github.com/Opticmix/opticmix-vision/issues, or write to
contact@opticmix.kr if you cannot post publicly. We reply within 2 business days
(Korea time). What to include: [python/README.md](python/README.md#getting-help).

## License

Apache-2.0. See [LICENSE](LICENSE). The compiled extension also contains Rust
crates under MIT / Apache-2.0; their notices are in
[python/THIRD_PARTY_NOTICES.txt](python/THIRD_PARTY_NOTICES.txt).
