# Changelog

All notable changes to this project are documented here.

## [0.0.2.0] - 2026-08-06

### Changed

- Skip existing `moe`, `mohw`, and `molit` documents before detail parsing and
  attachment downloads, and allow the attachment root to be configured with
  `RD2_FILES_ROOT` so EC2 can reuse its existing file store.

## [0.0.1.1] - 2026-08-06

### Changed

- Replace the `pip freeze` environment dump in `requirements.txt` with the 20 packages the code imports directly, so a fresh install no longer pulls conda tooling, build backends, and unused transitive pins.

## [0.0.1.0] - 2026-08-05

### Added

- Render synthetic payloads with consistent document presentation, list hierarchy, and source-preserving text cleanup.
- Name generated PDF files from their document titles when no explicit filename is supplied.

### Changed

- Apply the presentation context consistently across supported report, notice, meeting, and official-document templates.

### Fixed

- Prevent long official-document previews from repeating their title and full body before the continuation pages.
- Preserve source atoms and verify rendered documents across all supported template families.
