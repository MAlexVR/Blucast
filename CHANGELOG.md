# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.0] - 2026-08-30

This is the first release published from this fork. It's additive on top of
upstream `v1.0.7` — existing `settings.json` files and the `ghcr.io/andrei9383/blucast`
base image keep working unchanged.

### Added
- **Auto Reframe**: AI face tracking that automatically zooms and pans to keep
  you centered as you move, with a selectable detector (DNN SSD or Haar
  Cascade), and adjustable zoom amount / tracking speed.
- **Virtual Light**: a face-anchored brightening effect (approximates NVIDIA
  Broadcast's Virtual Key Light using classical face detection instead of AI
  relighting) with an intensity slider.
- **Mirror Preview**: flips the local preview only, independent of the
  transmitted video — matches how Zoom/Teams/Broadcast handle this.
- **Multi-language UI**: English and Spanish, selectable from the General tab
  (takes effect on restart).
- **Manual zoom/pan controls** with a compact on-screen D-pad, available
  alongside Auto Reframe.
- Tabbed control panel (Camera / Effects / Framing / General) so every
  setting is reachable without scrolling through unrelated sections.
- An About panel (General tab) with the app logo, name, and description.
- `Start at Login` is now its own clearly-labeled section in the General tab.

### Changed
- **Build no longer requires the NVIDIA Maxine SDK download.** `Containerfile`
  now pulls the compiled runtime libraries and models from the already-published
  `ghcr.io/andrei9383/blucast:latest` image, and only vendors the MIT-licensed
  Maxine headers (`app/third_party/`) needed to compile against them. The old
  "download the SDK from NGC, extract to `sdk/`" flow is only needed if you
  want to rebuild those runtime libraries from scratch yourself.
- All toggle buttons across the UI were replaced with a consistent iOS-style
  switch component (was a mix of checkable buttons).
- Default window height increased so the Quit button is visible without
  resizing.

### Fixed
- Virtual Light bleeding onto a replaced/blurred background — it's now gated
  by the segmentation matte so it only ever touches the actual person.
- A performance regression that could make the video freeze for seconds at a
  time when Virtual Light was combined with a high Auto Reframe zoom, caused
  by an unbounded blur kernel size.
- A redundant "Auto Reframe" label directly under the "AUTO REFRAME" section
  header.

### Removed
- An experimental AI "Eye Contact" (gaze redirection) feature was built,
  evaluated, and removed again during this cycle — the visual result
  (MediaPipe iris tracking + a small per-eye warp) didn't reach a quality bar
  worth shipping. No trace of it remains in the current build.

## [1.0.7] - 2026-05-04
### Added
- Support for 40xx & 50xx series GPUs.
- Initial Wayland support.

### Fixed
- `modinfo` now runs with elevated privileges where required.

## [1.0.6] - 2026-02-23
### Added
- Container image signing (cosign) workflow.
- Auto-detection of the first available `/dev/video*` device.
- Troubleshooting guidance for CDI generation and the GNOME AppIndicator
  extension requirement.

### Changed
- The virtual camera device is now opened write-only.
- Installer now also offers to run the uninstall script.

### Fixed
- Correct `v4l2loopback` virtual device loading order.
- Video format switched from BGR24 to YU12 for broader browser compatibility.
- WirePlumber is restarted around virtual camera setup so PipeWire picks up
  the new device reliably.
- Camera not starting after an app restart; an uninitialized streaming flag.
- Release image URI lowercased to match GHCR's repository naming requirement.

## [1.0.3] - 2026-02-14
### Fixed
- A display error on pure-Wayland hosts.

## [1.0.2] - 2026-02-13
### Changed
- Project renamed to BluCast throughout the codebase.

## [1.0.1] - 2026-02-13
### Added
- Manual input device switching.

### Fixed
- `libGL`/Mesa loading errors on some hosts.

## [1.0.0] - 2026-02-12
Initial public release.

### Added
- Background removal, background replacement (custom image), and background
  blur, powered by the NVIDIA Maxine VideoFX SDK.
- On-demand camera usage — the camera is only opened while something is
  actually consuming the virtual device.
- Live in-app preview.
- GHCR-based container publishing workflow.

[Unreleased]: https://github.com/MAlexVR/Blucast/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/MAlexVR/Blucast/compare/v1.0.7...v1.1.0
[1.0.7]: https://github.com/Andrei9383/Blucast/compare/v1.0.6...v1.0.7
[1.0.6]: https://github.com/Andrei9383/Blucast/compare/v1.0.3...v1.0.6
[1.0.3]: https://github.com/Andrei9383/Blucast/compare/v1.0.2...v1.0.3
[1.0.2]: https://github.com/Andrei9383/Blucast/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/Andrei9383/Blucast/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/Andrei9383/Blucast/releases/tag/v1.0.0
