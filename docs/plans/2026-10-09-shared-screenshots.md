# Shared screenshots through Asset Library

## Scope
- Default on: upload each prepared image once, reuse its library ID across selected version localizations and PPO treatment localizations.
- Keep an explicit legacy-upload switch for compatibility; no silent fallback or repeated binary uploads.
- Preserve automatic crop, older device groups, file order, locale selection, four upload workers, and error summaries.
- Read Apple's reference catalog at runtime. Resolve placement groups by their documented display class and platform, not hard-coded group IDs.
- Validate all files and group limits before upload. Wait for image processing and verify spec IDs before modifying placements.
- Replace only screenshot placements in targeted groups; never delete shared library images during placement replacement.
- Restore previous placements after a failed replacement when possible; report the affected locale if rollback fails.
- Check Downloads locally without uploading or deleting the user's source files. No Duo support in this change.

## Implementation
1. Add a separately testable Asset Library client/catalog module with upload, polling, placement replacement, ordering and verification.
2. Integrate the shared flow into the screenshot worker and PPO workers; preserve the legacy branch when disabled.
3. Add a checked-by-default UI option and update counts, progress and help.
4. Test failure paths, upload-once reuse, both worker families, current Downloads and UI defaults.
5. Review, rebuild the desktop application, and verify launch.

## Validation focus
- Unsupported catalog, file dimensions, alpha, size and count limits: no destructive writes.
- Failed or mismatched image processing: old placements unchanged.
- Partial placement replacement and ambiguous POST failure: best-effort restoration, never shared-asset deletion.
- Pagination, localization ordering, and screenshots mixed with previews/other device groups.
- No implicit network upload in local Downloads inspection.
