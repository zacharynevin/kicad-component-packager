# Editable KiCad PCM ZIP

Packager saves and exports one ZIP for both KiCad installation and continued editing. There is no separate working-file format or legacy-format importer.

## Archive layout

```text
metadata.json                         # KiCad PCM v2 package metadata
symbols/<library>.kicad_sym            # selected component revisions
footprints/<library>.pretty/*.kicad_mod
3dmodels/<library>.3dshapes/*
resources/documents/*
resources/sources/*
resources/assembly-bom.csv
resources/component-index.json
resources/packager/manifest.json       # editable catalog snapshot, version 1
resources/packager/objects/<sha256>     # additional unique payloads
```

The ordinary KiCad files contain one selected revision per component ID. Model and local document links target the PCM installation paths. KiCad library nicknames use the configured prefix (default `PCM_`). Nested Packager library names are flattened for KiCad, such as `Vox_Adafruit_Diodes`. A collection containing only empty folders includes an empty symbol library so it still has a PCM content directory.

## Editable snapshot

`resources/packager/manifest.json` contains:

- `format`: `kicad-component-packager`; `format_version`: `1`.
- `name` and `options`: package name, identifier, version, author, license and library prefix.
- `libraries`: the full library registry, including names, paths, parents and empty folders.
- `selected`: sorted `[component ID, revision]` pairs installed by KiCad.
- `components`: a record for every existing revision up to each selected revision, containing its ID, revision, digest and original file-path/checksum index.
- `objects`: each referenced SHA-256 mapped to its archive path. Matching native models, documents and source files are reused. Other payloads are stored once under `resources/packager/objects/`.
- `extra_files`: source reports and unreferenced native models retained in full saves; omitted from filtered exports.
- `pcm_files`: SHA-256 checksums of all native PCM files outside the snapshot.

Component metadata, normalized geometry, original source geometry, model transforms, header recipes and assembly requirements are retained byte for byte. The component digest is the SHA-256 of its canonical file inventory: UTF-8 JSON, two-space indentation, sorted keys, literal Unicode and a trailing newline. Application window preferences, filters, active selections, project workspaces and Recently deleted items are not collection data and are not saved.

Export sorts entries and uses fixed ZIP timestamps. Re-exporting unchanged content and settings yields identical bytes in the same compression environment. Shared editable data is deduplicated, though KiCad may require native files in more than one library folder.

## Reopening and validation

Import verifies all native and editable payload checksums, component identity and digests, declared assets, geometry, pin/pad identifiers, selected revisions, and the library hierarchy before making changes. Missing or malformed snapshots fail validation instead of falling back to a generic import. Unindexed files are rejected. Native PCM library ZIPs without a snapshot use the desktop native-library importer. Other source ZIPs use the ordinary component importer. Native library entries may have a symbol, a footprint, or both; pin/pad identifiers are preserved without requiring them to match. Bundled models and documents must resolve, and unavailable external source datasheets are retained in original metadata with a warning.

Reopening merges into the local catalog. Identical revisions are reused; an existing revision with different contents is rejected. Newer local revisions remain current. A fresh catalog receives the exact saved revision history and library tree. New revisions, library registry and package options are rolled back if publication fails. Imported component IDs retain their identity, so the next edit continues their revision sequence.

Desktop PCM archives are limited to 8 GiB unpacked, 300,000 files, and 512 MiB per individual asset. Browser uploads and ordinary source imports retain the 128 MiB / 10,000-entry limit. Desktop import/export streams large collections through disk-backed, content-addressed assets. Export rejects collections beyond the corresponding import limits. Source reads also reject traversal paths, symlinks and case-conflicting filenames.

Checksums detect corruption; they do not authenticate the publisher. Original source notices remain bundled. No package scripts or installer hooks are executed.

See [KiCad’s PCM documentation](https://dev-docs.kicad.org/en/addons/) for the native package structure.
