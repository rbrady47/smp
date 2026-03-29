# Changelog

All notable SMP changes should be documented here in markdown.

The format is intentionally simple so diffs stay readable in version control.

## Unreleased

### Added

- Initial markdown changelog tracking.
- Initial version-controlled user guide in [docs/USER_GUIDE.md](docs/USER_GUIDE.md).
- Operational-map backend foundation for authored SNMPc-style map workflows:
  - map view models and schemas
  - map object and link models and schemas
  - object and link binding support
  - operational-map API routes

### Changed

- Documented the distinction between the legacy fixed-layout topology page and the newer authored Operational Maps direction.
- Established the documentation policy that user-facing changes should be reflected in markdown docs and changelog entries.
- Node Dashboard node health now fails closed when SMP loses current reachability, so disconnected or unreachable ANs and DNs stop presenting stale `Up`, RTT, Web, SSH, and traffic state.

### Notes

- Operational Maps are under active development and should be treated as the future authored-map system.
- The fixed `/topology` page remains available as a transitional operational view.
