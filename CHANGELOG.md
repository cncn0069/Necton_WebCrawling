# Changelog

All notable changes to this project are documented here.

## [0.0.1.0] - 2026-08-05

### Added

- Render synthetic payloads with consistent document presentation, list hierarchy, and source-preserving text cleanup.
- Name generated PDF files from their document titles when no explicit filename is supplied.

### Changed

- Apply the presentation context consistently across supported report, notice, meeting, and official-document templates.

### Fixed

- Prevent long official-document previews from repeating their title and full body before the continuation pages.
- Preserve source atoms and verify rendered documents across all supported template families.
