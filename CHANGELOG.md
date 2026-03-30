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
- A `Services` cloud object on `/topology` that can be moved and resized in edit mode and derives its status from the services pinned to the main dashboard watchlist.
- Persisted the `/topology` demo-mode selector so edit-mode preview states survive reloads and backend editor-state refreshes.

### Changed

- Documented the distinction between the legacy fixed-layout topology page and the newer authored Operational Maps direction.
- Established the documentation policy that user-facing changes should be reflected in markdown docs and changelog entries.
- Node Dashboard node health now fails closed when SMP loses current reachability, so disconnected or unreachable ANs and DNs stop presenting stale `Up`, RTT, Web, SSH, and traffic state.
- The topology detail drawer now exposes pinned-service counts and watchlist members for the new services cloud object.

### Notes

- Operational Maps are under active development and should be treated as the future authored-map system.
- The fixed `/topology` page remains available as a transitional operational view.
