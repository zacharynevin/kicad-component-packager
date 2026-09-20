# Core Libraries for KiCad 10

This package preserves the complete standard KiCad 10.0.3 symbol and footprint
selection, reorganizes symbols into vendor, generic and functional libraries,
and includes every 3D model present in the source installation. It is an
independently prepared collection, not an official KiCad release.

## Install

1. In KiCad 10, open **Plugin and Content Manager → Install from File** and select
   `Core-Libraries-1.0.0-KiCad10.zip`.
2. Enable automatic symbol and footprint library registration with the prefix
   **PCM_** in KiCad preferences. All supplied footprint links use that prefix.
3. Browse `PCM_Core_Generic_…`, `PCM_Core_Vendors_…` and `PCM_Core_Functions_…`.
   `PCM_Core_Utilities_…` contains power and other non-purchasable symbols.

The ZIP includes symbols, footprints and available models; colleagues do not
need the standard symbol, footprint or model libraries for these entries.
If automatic registration is disabled, `sym-lib-table` and `fp-lib-table` in the
installed resources folder provide the entries to add. **Merge** them with an
existing table instead of overwriting other libraries.

For existing designs, retain their current libraries until their references
are deliberately migrated. The included `migration-map.json` maps each original
identifier to its new identifier. No existing designs are modified by this build.

## Browse and review

Open `Core-Library-Inventory.html` alongside the ZIP, or `resources/index.html`
after extracting it. It works offline and searches symbols and footprints by
manufacturer, name, original identifier, description and metadata status.

Manufacturer fields derived from datasheet hosts or names are labelled as
inferred in **Core Metadata Evidence**. Existing data is retained. **MPN
Candidate** is a search aid from the KiCad identifier, not an approved order
code; **MPN** remains blank unless explicitly supplied or checked for a curated
variant. Many symbols represent families or omit ordering suffixes.

Datasheet URLs are retained and exposed, but have not all been checked for
availability or exact-part applicability. Vendor websites are a starting point
for confirming the part. The Hirose DM3AT-SF-PEJM5 supplemental variant links the
manufacturer's product page and drawing to the matching existing KiCad
footprint/model. Its pin numbers match the footprint; its geometry is inherited
from KiCad, not newly certified against manufacturing tolerances.

## Generic BOM entries

Generic resistors, capacitors, inductors and headers are **specification-based**
and allow equivalent substitutions only when all design specifications are met.
No manufacturer, order code, value or rating is invented. Fill in the added
fields when using a symbol in a design:

- Resistors: resistance, tolerance, package, power; voltage and temperature
  coefficient where needed. Networks must also match topology and pinout.
- Capacitors: capacitance, tolerance, package, voltage and dielectric/type;
  polarity, DC-bias behaviour, ESR, ripple current and temperature where needed.
- Inductors: inductance, tolerance, package, rated and saturation current, DCR;
  shielding, frequency, core and coupled winding requirements where needed.
- Headers: positions, rows, pitch, gender, mounting, orientation and mating
  dimensions; pin numbering, plating and current requirements.

Blank fields mean **unspecified**, not unlimited. Use manufacturer-specific
entries when a circuit relies on particular device characteristics. Export
`Sourcing`, `Substitutions`, `BOM Requirements` and the relevant ratings with a
design BOM. The inventory itself is a library catalog, not a design BOM.

## Completeness and provenance

Every original symbol remains selectable, including generic symbols with no
fixed footprint and utility symbols that do not belong in the BOM. Symbol
inheritance is flattened without changing pins, drawings or ERC flags, enabling
independent vendor grouping. All footprint pads and drawing geometry are
preserved. A build-time fingerprint checks this for each original symbol.

Not every upstream footprint has an available model. Unavailable model links
are recorded in **Core Missing Models** and `missing-assets.json` and omitted
from active model references. This avoids broken external dependencies while
making the gaps visible. A footprint with missing 3D remains usable for layout;
check **Core 3D Status** before relying on the package for mechanical clearance.
No visually similar model is substituted for a missing exact model.

The source inventory and SHA-256 hashes are in `source-manifest.json`.
The package includes the full KiCad library license notices and original
embedded attributions. Changes to organization and metadata are distributed
under the same CC BY-SA 4.0 license with KiCad's design exception.

Upstream libraries and documentation:

- https://gitlab.com/kicad/libraries/kicad-symbols
- https://gitlab.com/kicad/libraries/kicad-footprints
- https://gitlab.com/kicad/libraries/kicad-packages3D
- https://www.kicad.org/libraries/license/
- https://dev-docs.kicad.org/en/addons/
- https://www.hirose.com/product/p/CL0609-0031-0-00

Build source lives in `tools/core_library/` of the Component Packager workspace.
Run `python3 tools/core_library/build.py` against the installed KiCad 10 source.
In Component Packager 0.1.10+, use **Import components** and choose this ZIP.
The desktop importer preserves the entire selection, including unpaired generic
and utility symbols, standalone footprints, models and source reports.
Use **Has footprint** and **Has 3D model** (or both), then **Export matching**
to make a smaller PCM ZIP. **Save ZIP** preserves the full catalog and its tree
for both KiCad installation and later reopening in Packager.
