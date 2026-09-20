# KiCad Component Packager desktop preview

The desktop application has its own window, application menus, native Open/Save dialogs, and a persistent local catalog. Python and the browser runtime are bundled. It does not start an HTTP server.

## Open the application

Fresh installations start with an empty catalog and no library folders. Demo components and Core libraries are not bundled or imported automatically. Updates retain your existing local catalog, so components you previously imported will still appear.

- **Mac, Apple Silicon:** Extract `KiCad-Component-Packager-0.1.11-macOS-AppleSilicon.zip` and open `KiCad Component Packager.app`. You can move the app to Applications. Tested on macOS 15.3.1; Intel Macs use the separate macOS-Intel release ZIP.
- **Windows, x64:** Extract **the entire** `KiCad-Component-Packager-0.1.11-Windows-x64.zip`, then open `KiCad Component Packager.exe` inside its folder. Keep the adjacent files and `resources` folder together. Python and Node do not need to be installed.

These are local preview builds. The Mac app has a verified ad hoc signature, without Apple Developer ID signing or notarization. The Windows executable has no publisher certificate. Public distribution signing and an installer are future release steps.

Install **KiCad 10+** in its normal location for Eagle/Altium conversion and symbol/footprint previews. Interactive STEP/WRL viewing is bundled locally. KiCad Component Packager also searches PATH and honors `KICAD_COMPONENT_PACKAGER_KICAD_CLI` when supplied by the launching environment.

## Use the library manager

1. Choose **File → Import Components** and select library files, a folder, or a saved PCM ZIP. Public GitHub and direct library URLs can be pasted into the import dialog.
2. Watch the import progress: downloaded bytes, files read, components checked, and components imported. Each stage has its own bar; downloads without a total size and KiCad conversion remain indeterminate. Review a matched symbol and footprint, then save the component to the catalog.
3. Create libraries with **+** beside Libraries. Choose a parent for a sub-library; selecting a parent includes its children.
4. Select a component row. The inspector shows its symbol, footprint, 3D models, properties, bundled assets, and source details.
5. Choose **Export ZIP…**. The Include picker offers the entire collection, the current filtered view, checked components, or the component and revision shown in the inspector. The same ZIP installs through KiCad PCM and reopens in Packager.

Eagle XML `.brd` repositories (including Adafruit Qualia) open **Create module from board**. Review the suggested header pads in the top view, edit signal labels, set carrier header drill/pad sizes, and choose mounting-hole guides or unplated holes. The result is one module symbol and footprint; its outline is on F.Fab. This workflow supports header-mounted modules, not castellated/SMD interfaces. Include the `.brd`; a `.sch` alone has no pad positions.

Use **Delete…** in the inspector or **Delete selected…** above checked rows to move components and all their revisions to **Recently deleted**. The sidebar opens their recovery list. Restore or permanently delete an individual component or a group. Restoring returns all revisions; installed copies and saved collections remain intact. Deleted IDs can be reimported. Restoring an older deleted copy never overwrites a newly imported component with the same ID.

Libraries appear as folders in the sidebar, with categories beneath them. Select a folder to filter the list; the Library column remains visible in All components. Check rows or Shift-click to select a range. The selection bar offers **Move to library…**, **Edit properties…**, and **Delete selected…**. Use the toolbar’s **Export ZIP… → Checked components** to export the selection. Only checked fields are applied in the bulk editor.

In the inspector, **3D model** shows the body against the pads. **Align / attach 3D models…** opens a STEP/STP/WRL file picker and XYZ offset (mm), rotation (degrees) and scale controls. Offsets, rotations, and scale update immediately in the local 3D viewport. Drag or scroll to pan, pinch or Ctrl+wheel to zoom, and right-drag to orbit. **Fit** resets the view. Symbol and footprint previews also support pan, pinch/zoom, and Fit. Choose **Save new revision** when ready. The preview shows footprint pads and outlines as an alignment reference; it does not edit a project PCB.

Board-derived modules also offer **Automatic module headers** in the model editor. Male pins and female sockets are generated at the connection pads. The model is generic geometry; the PCM ZIP includes an assembly BOM with quantities per module, dimensions and optional part number/vendor URL. Generic connectors allow equivalent substitutions. These are supplemental assembly requirements, not independently placed schematic components.

Use **Edit properties…** in the inspector to correct a name, manufacturer, part number, website, datasheet, description, category, library, or notes. Saving changed values creates a new revision. Earlier revisions remain available; unchanged values do not create a duplicate revision.

**Export ZIP…** is the single export action in the toolbar and File menu. Choose **Entire collection** to include all components and empty folders, or choose a narrower scope. The local catalog saves changes automatically. Every export uses the same PCM ZIP: install it through KiCad 10’s **Plugin and Content Manager → Install from File**, or import it back into Packager to restore the library tree, properties, models, sources and revision history. Reopening merges with the catalog, reusing identical revisions and rejecting conflicts. Existing newer local revisions remain current. Empty folders and package options are preserved; Recently deleted items and window settings are not exported. Desktop PCM ZIPs support 8 GiB unpacked, 300,000 files and 512 MiB per asset, using disk-backed storage for large catalogs. Browser uploads and ordinary source imports remain limited to 128 MiB / 10,000 entries.

Open the Core PCM ZIP with **Import components** to preserve its entire symbol selection, standalone footprints and linked 3D models. Generic symbols may have no footprint assigned; they remain in the catalog.

Check **Has footprint**, **Has 3D model**, or both to filter by bundled assets. **Export ZIP… → Current view** exports every match across all pages, including the current library and search filters. **Entire collection** includes everything regardless of filters. Navigating to a parent folder includes all descendants and starts at the first page; explicit asset filters remain active. Tables use 200 entries per page.

Click column headings to sort. Use ↑/↓ to browse rows and the sidebar or search field to filter. Drag the divider to resize the inspector, or focus it and use ←/→. Inspector tabs also support ←/→.

| Shortcut | Action |
| --- | --- |
| ⌘/Ctrl+F | Search components |
| ⌘/Ctrl+I | Import components |
| ⌘/Ctrl+O | Open a library collection |
| ⌘/Ctrl+R | Refresh the library |
| ⌘/Ctrl+S | Export ZIP… |

The desktop catalog is separate from the browser/CLI catalog. To transfer an existing catalog, save a saved PCM ZIP from the browser, or export one through the CLI and import it into the app. Demonstration source files are in `examples/vendor` in the repository.

## Storage

New installations keep catalogs and settings in `~/Library/Application Support/KiCad Component Packager/` on macOS and `%APPDATA%\KiCad Component Packager\` on Windows. On upgrade from PartShelf, the app reuses the existing `PartShelf` data directory when no directory under the new name exists. Nothing is moved or rewritten during this selection; existing catalogs, recent projects, and window settings remain available. **File → Show Catalog Folder** reveals the catalog. Project libraries are stored in the project folder you selected. Moving or closing the app does not remove saved data. The last project and recent-project list survive restarting the app.

## Build

See [the macOS and Windows build instructions](building.md) for local development, packaging, tests and automated GitHub releases.

## Multiple open projects (0.1.4)

Use **File → Direct Install into KiCad Project…** to reveal optional project controls, then the + beside the project tabs. **Back to library packaging** hides them. The desktop file chooser accepts several `.kicad_pro` files at once. Each project stays in the sidebar and tab strip until you close it. Click a tab to activate its project; Ctrl+Tab cycles through open projects. Arrow keys move between focused tabs.

Search, component selection, checkboxes, inspector tab, sorting, library filters, and the current page are remembered separately for each project. The open project list and active project survive application restarts. Close a project with its tab’s × or File → Close Active Project (⌘/Ctrl+Shift+W). Closing removes it from the workspace and preserves its files, components, and remembered view.

Install reviews display the target project and folder. Install, repair, and project ZIP export are bound to that project; changing the active project invalidates an outstanding operation. Components remain in one shared catalog. A missing drive or damaged project is marked as needing attention and does not prevent switching to another project.

## Moving and organizing components

Select component checkboxes and choose **Move to library…**. Choose an existing
library, or enter a new sub-library name such as `Diodes` beneath `Vox / Adafruit`.
You can also drag selected rows or a whole **Components** or **Modules** group onto
a library. Components is the new display name for previously Unsorted entries.

Possible duplicates are detected by matching names or matching manufacturer and
part number. Review their IDs, revisions and model counts before choosing:

- **Create new revision:** keep the destination ID and prior revisions; use the
  incoming contents for its next revision. The incoming entry goes to Recently deleted.
- **Overwrite destination entry:** keep the incoming ID and history, and move
  the replaced destination entry and its history to Recently deleted.
- **Keep both:** retain both separate entries.

Ordinary moves preserve the component ID and previous revisions. A move never
rewrites a saved revision or changes a component already installed in a design.
The component table includes sortable Part number and 3D attachment status columns.

## Short and stacking module headers

Under **Automatic module headers**, select male pins or female sockets and
**Regular / short** or **Long / stacking**. Presets supply editable starting
dimensions, not exact manufacturer specifications. Male stacking extends the
exposed mating pins; female stacking extends the through-board solder tails.
Set top/bottom mounting, PCB thickness, housing height and solder-tail length.
Bottom mounting puts the male mating pins beneath the module, with short tails
through its PCB. The calculated opposite-side protrusion is shown beside the
controls, and all dimensions are included in the assembly BOM. Existing header
placements retain their mounting side until explicitly changed.

## Vendor datasheet placeholders

When a vendor export repeats its part name or number in the Datasheet field, import continues with a missing-datasheet note. The original value remains in the source record; no URL or document is fabricated. Real missing document and model links still require their linked assets. Component search appears only for sources with multiple symbols and uses a magnifying-glass icon.

Component, collection, and board-module imports include a Destination library picker with the full library hierarchy. The destination stays selected when switching components or returning from Preview. Choose New top-level library to name a new folder during import.
