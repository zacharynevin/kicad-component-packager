import base64
import copy
import io
import json
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest import mock
import zipfile

from partshelf import projects, sexpr as sx
from partshelf.components import Catalog, Source, properties, resolve_symbol, set_property
from partshelf.files import PartShelfError, canonical, digest, inventory, load_source, sha
from partshelf.packages import export_package, import_package, inspect_package, is_package, MANIFEST, OBJECTS
from partshelf.sources import direct_source, fetch_source, public_url, provenance_url
from partshelf.web import Application
from partshelf.desktop import DesktopApplication
from partshelf.pcm import export_pcm
from partshelf.models import edit_models, model_settings, render_model
from partshelf import trash
from partshelf.collections import collection_name, selections_from_query
from partshelf.conversion import find_cli

VENDOR = Path(__file__).resolve().parents[1] / "examples" / "vendor"


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="partshelf-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.catalog = Catalog(self.root / "catalog")
        self.project = self.root / "project"
        self.source_files = load_source(VENDOR)
        self.source = Source.read(self.source_files)
        self.choices = {c["name"]: c for c in self.source.inspect()["symbols"]}

    def prepare(self, name="Resistor_10k", part_id="test.resistor", **metadata):
        choice = self.choices[name]
        return self.source.prepare(choice["key"], choice["footprint"], {"id": part_id, **metadata})

    def publish(self, **metadata):
        return self.catalog.publish(*self.prepare(**metadata))

    def unpack(self, data):
        path = self.root / "input.zip"
        path.write_bytes(data)
        return load_source(path)

    def test_recently_deleted_items_can_be_readded_restored_individually_or_purged(self):
        first = self.publish()
        app = Application(self.root, self.catalog.root)
        app.post("delete-components", {"components": [{"id": first["id"], "revision": first["revision"]}]})
        replacement = self.catalog.publish(*self.prepare())
        self.assertEqual(replacement["revision"], 1)
        entries = trash.deleted(self.catalog)
        self.assertEqual(len(entries), 1)
        with self.assertRaisesRegex(PartShelfError, "already exists"):
            trash.restore(self.catalog, entries[0]["id"], first["id"])
        result = trash.purge(self.catalog, entries[0]["id"], first["id"])
        self.assertEqual(result["purged"], 1)
        self.assertEqual(trash.deleted(self.catalog), [])

    def test_library_creation_supports_flattened_sub_library_names(self):
        app = Application(self.root, self.catalog.root)
        parent = app.post("create-library", {"name": "Adafruit"})
        child = app.post("create-library", {"name": "Core", "parent": parent["path"]})
        self.assertEqual(child["path"], "Adafruit_Core")
        self.assertTrue({"Adafruit", "Adafruit_Core"}.issubset({item["path"] for item in self.catalog.libraries()}))


class ModelAndCollectionTests(WorkspaceTest):
    def test_model_placement_revisions_leave_installed_project_unchanged(self):
        first = self.publish()
        projects.initialize(self.project)
        projects.install(self.project, self.catalog, first["id"], 1)
        original, files, integrity = self.catalog.read(first["id"], 1)
        meta, edited = edit_models(original, files, {"models": [{"index": 0, "offset": [1.2, -2.3, 0.8], "rotate": [0, 0, 90]}]})
        second = self.catalog.publish(meta, edited)
        self.assertEqual(second["revision"], 2)
        placement = model_settings(meta, edited)[0]
        self.assertEqual(placement["offset"], [1.2, -2.3, 0.8])
        self.assertEqual(placement["rotate"], [0, 0, 90])
        self.assertEqual(placement["scale"], model_settings(original, files)[0]["scale"])
        self.assertEqual(self.catalog.read(first["id"], 1)[2], integrity)
        self.assertTrue(projects.verify(self.project)["ok"])
        self.assertEqual(projects.status(self.project)["components"][0]["revision"], 1)
        archive = self.unpack(export_pcm(self.catalog, [(first["id"], 2)]))
        footprint = sx.loads(next(data.decode() for name, data in archive.items() if name.startswith("footprints/") and name.endswith(".kicad_mod")))
        self.assertEqual(sx.child(sx.child(footprint, "model"), "offset"), sx.loads("(offset (xyz 1.2 -2.3 0.8))"))

    def test_step_attachment_preserves_bytes_and_transform_across_save(self):
        original, files = self.prepare()
        # Header fixture tests byte transport; native render coverage uses real geometry.
        step = b"ISO-10303-21;\nHEADER; ENDSEC; DATA; ENDSEC; END-ISO-10303-21;"
        meta, edited = edit_models(original, files, {"attachment": {"name": "vendor-body.step", "data": base64.b64encode(step).decode(), "replace": 0}})
        self.assertEqual(model_settings(meta, edited)[0]["offset"], model_settings(original, files)[0]["offset"])
        self.assertEqual(len(meta["assets"]["models"]), 1)
        path = meta["assets"]["models"][0]
        self.assertEqual(edited[path], step)
        self.assertNotIn(original["assets"]["models"][0], edited)
        self.catalog.publish(meta, edited)
        saved = self.unpack(export_package(self.catalog))
        _, parts = inspect_package(saved)
        self.assertEqual(parts[0][1][path], step)
        self.assertEqual(parts[0][0]["model_names"][path], "vendor-body.step")

    def test_invalid_model_inputs_fail_without_mutating_source(self):
        meta, files = self.prepare()
        before = copy.deepcopy((meta, files))
        for change in ({"offset": [float("nan"), 0, 0]}, {"scale": [1, 0, 1]}, {"offset": [0, 0]}, {"rotate": [0, 10001, 0]}):
            with self.assertRaises(PartShelfError):
                edit_models(meta, files, {"models": [{"index": 0, **change}]})
        with self.assertRaises(PartShelfError):
            edit_models(meta, files, {"attachment": {"name": "bad.step", "data": base64.b64encode(b"not STEP").decode()}})
        self.assertEqual((meta, files), before)

    def test_library_grouping_recovers_source_without_rewriting_revisions(self):
        meta, files = self.prepare()
        meta["provenance"]["source"] = {"repository": "adafruit/Adafruit-Eagle-Library"}
        part = self.catalog.publish(meta, files)
        app = Application(self.root, self.catalog.root)
        self.assertEqual(app.state()["catalog"][0]["collection"], "Adafruit")
        desktop = DesktopApplication(self.root / "desktop-data")
        desktop.catalog = self.catalog
        self.assertEqual(desktop.state()["catalog"][0]["collection"], "Adafruit")
        self.assertEqual(self.catalog.revisions(part["id"]), [1])
        self.assertNotIn("collection", self.catalog.read(part["id"], 1)[0])
        self.assertEqual(collection_name({"collection": "Custom library", "provenance": meta["provenance"]}), "Custom library")

    def test_bulk_properties_change_only_selected_fields_and_parts(self):
        app = Application(self.root, self.catalog.root)
        for name in self.choices:
            self.catalog.publish(*self.prepare(name=name, part_id="selected." + name.lower()))
        selected = self.catalog.list()[:2]
        original = self.catalog.list()[2]
        result = app.post("edit-properties", {"components": selected, "fields": {"manufacturer": "Shared vendor", "collection": "My modules", "category": "Modules"}})
        self.assertEqual(result, {"updated": 2, "unchanged": 0})
        for part in selected:
            meta, files, _ = self.catalog.read(part["id"], 2)
            self.assertEqual(meta["collection"], "My modules")
            self.assertEqual(meta["manufacturer"], "Shared vendor")
            self.assertEqual(meta["name"], part["name"])
            self.assertEqual(meta["mpn"], part["mpn"])
            symbol = sx.children(sx.loads(files["symbol.kicad_sym"].decode()), "symbol")[0]
            self.assertEqual(properties(symbol)["Manufacturer"], "Shared vendor")
        self.assertEqual(self.catalog.revisions(original["id"]), [1])
        with self.assertRaises(PartShelfError):
            app.post("edit-properties", {"components": [original, selected[0]], "fields": {"notes": "Must not apply"}})
        self.assertEqual(self.catalog.revisions(original["id"]), [1])

    def test_selected_native_export_and_reimport_exclude_bundled_originals(self):
        for name in self.choices:
            self.catalog.publish(*self.prepare(name=name, part_id="native." + name.lower(), collection="Demo library"))
        selected = [[p["id"], p["revision"]] for p in self.catalog.list()[:2]]
        query = {"selections": [json.dumps(selected)]}
        archive = self.unpack(export_pcm(self.catalog, selections_from_query(query)))
        index = json.loads(archive["resources/component-index.json"])
        self.assertEqual(len(index["components"]), 2)
        source = Source.read(archive)
        self.assertEqual(len(source.symbols), 2)
        self.assertEqual(len(source.footprints), 2)
        self.assertTrue(all(part["mapping"]["ok"] for part in source.inspect()["symbols"]))

    @unittest.skipUnless(find_cli(), "KiCad is required for native 3D rendering")
    def test_native_3d_render_changes_when_model_moves(self):
        meta, files = self.prepare()
        first = render_model(files, "top")
        _, moved = edit_models(meta, files, {"models": [{"index": 0, "offset": [2, 0, 0.4]}]})
        second = render_model(moved, "top")
        self.assertNotEqual(first, second)
        self.assertTrue(base64.b64decode(first.split(",")[1]).startswith(b"\x89PNG\r\n\x1a\n"))


class DesktopTests(WorkspaceTest):
    def test_native_project_outside_workspace_persists(self):
        app = DesktopApplication(self.root / "desktop-data")
        app.post("create-project", {"path": str(self.project)})
        reopened = DesktopApplication(self.root / "desktop-data")
        self.assertEqual(reopened.project, self.project.resolve())
        self.assertIn(str(self.project.resolve()), reopened.state()["projects"])
        self.assertTrue(reopened.state()["project"]["ok"])

    def test_desktop_export_preserves_the_complete_component(self):
        app = DesktopApplication(self.root / "desktop-data")
        original = app.catalog.publish(*self.prepare())
        destination = self.root / "saved package.zip"
        result = app.request("export", {"source": "package?id=test.resistor&revision=1", "destination": str(destination)})
        self.assertEqual(result["bytes"], destination.stat().st_size)
        manifest, items = inspect_package(load_source(destination))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0][0]["id"], original["id"])
        self.assertEqual(items[0][1][items[0][0]["assets"]["models"][0]], self.source_files["models/passive-0603.wrl"])
        with self.assertRaises(PartShelfError):
            app.request("export", {"source": "package", "destination": "relative.zip"})

    def test_native_import_and_invalid_settings_recover(self):
        data = self.root / "desktop-data"
        data.mkdir()
        (data / "settings.json").write_text("[]")
        app = DesktopApplication(data)
        original_workspace = app.workspace
        result = app.native_inspect([str(VENDOR)])
        self.assertEqual(result["kind"], "source")
        self.assertEqual(len(result["symbols"]), 3)
        self.assertEqual(app.workspace, original_workspace)
        with self.assertRaises(PartShelfError):
            app.native_inspect([])


class ComponentTests(WorkspaceTest):
    def test_selected_component_keeps_header_and_leaves_library_unchanged(self):
        library = next(iter(self.source.libraries.values()))
        library.append(sx.loads('(vendor_metadata (format "preserve me"))'))
        before = copy.deepcopy(self.source.libraries)
        for name in ("Resistor_10k", "Capacitor_100n"):
            _, files = self.prepare(name, "test." + name.lower())
            for asset in ("symbol.kicad_sym", "sources/original.kicad_sym"):
                prepared = sx.loads(files[asset].decode())
                self.assertEqual(len(sx.children(prepared, "symbol")), 1)
                self.assertEqual(sx.child(prepared, "vendor_metadata"), sx.child(library, "vendor_metadata"))
        self.assertEqual(self.source.libraries, before)

    def test_assets_and_transform_survive_import(self):
        meta, files = self.prepare()
        self.assertEqual(meta["pins"], ["1", "2"])
        model = sx.child(sx.loads(files[meta["assets"]["footprint"]].decode()), "model")
        self.assertEqual(sx.value(model[1]), meta["assets"]["models"][0])
        self.assertEqual(sx.child(model, "offset"), sx.loads("(offset (xyz 0 0 0.4))"))
        self.assertEqual(files[meta["assets"]["models"][0]], self.source_files["models/passive-0603.wrl"])
        self.assertIn("sources/original.kicad_sym", files)

    def test_local_document_is_bundled(self):
        meta, files = self.prepare("Header_1x04", "test.header")
        self.assertEqual(len(meta["assets"]["documents"]), 1)
        self.assertEqual(files[meta["datasheet"]], self.source_files["docs/header-notes.txt"])

    def test_part_number_datasheet_placeholder_does_not_block_import(self):
        choice = self.choices["Resistor_10k"]
        symbol = self.source.symbols[choice["key"]]["node"]
        set_property(symbol, "MPN", "10164359-00011LF")
        set_property(symbol, "Datasheet", "10164359-00011LF")
        meta, files = self.prepare(datasheet="10164359-00011LF")
        self.assertEqual(meta["datasheet"], "")
        self.assertEqual(meta["assets"]["documents"], [])
        self.assertEqual(meta["provenance"]["original_datasheet"], "10164359-00011LF")
        self.assertIn("Datasheet not provided", meta["provenance"]["warnings"][0])
        original = sx.child(sx.loads(files["sources/original.kicad_sym"].decode()), "symbol")
        self.assertEqual(properties(original)["Datasheet"], "10164359-00011LF")
        published = self.catalog.publish(meta, files)
        self.assertEqual(published["mpn"], "10164359-00011LF")
        for placeholder in ("Resistor_10k", properties(symbol)["Value"]):
            meta, _ = self.prepare(datasheet=placeholder)
            self.assertEqual(meta["datasheet"], "")

    def test_placeholder_named_document_is_bundled_when_present(self):
        self.source.files["Resistor_10k"] = b"Actual extensionless document"
        meta, files = self.prepare(datasheet="Resistor_10k")
        self.assertEqual(files[meta["datasheet"]], b"Actual extensionless document")
        self.assertNotIn("warnings", meta["provenance"])

    def test_real_missing_datasheet_and_ambiguous_files_remain_errors(self):
        for reference in ("Resistor_10k.pdf", "docs/Resistor_10k", "missing.pdf"):
            with self.subTest(reference=reference), self.assertRaisesRegex(PartShelfError, "Missing linked asset"):
                self.prepare(datasheet=reference)
        self.source.files.update({"a/Resistor_10k": b"one", "b/Resistor_10k": b"two"})
        with self.assertRaisesRegex(PartShelfError, "More than one"):
            self.prepare(datasheet="Resistor_10k")

    def test_missing_model_is_reported(self):
        files = {p: b for p, b in self.source_files.items() if p != "models/passive-0603.wrl"}
        source = Source.read(files)
        choice = self.choices["Resistor_10k"]
        with self.assertRaisesRegex(PartShelfError, "Missing linked asset"):
            source.prepare(choice["key"], choice["footprint"], {"id": "test.bad"})

    def test_wrong_footprint_is_rejected(self):
        with self.assertRaisesRegex(PartShelfError, "Pins without pads|Pads without pins"):
            self.source.prepare(self.choices["Header_1x04"]["key"], self.choices["Resistor_10k"]["footprint"], {"id": "test.bad"})

    def test_duplicate_model_basename_is_not_guessed(self):
        files = {p: b for p, b in self.source_files.items() if p != "models/passive-0603.wrl"}
        files["a/passive-0603.wrl"] = b"first"
        files["b/passive-0603.wrl"] = b"second"
        choice = self.choices["Resistor_10k"]
        with self.assertRaisesRegex(PartShelfError, "More than one"):
            Source.read(files).prepare(choice["key"], choice["footprint"], {"id": "test.bad"})

    def test_external_spice_model_is_explicitly_rejected(self):
        choice = self.choices["Resistor_10k"]
        set_property(self.source.symbols[choice["key"]]["node"], "Sim.Library", "vendor.spice")
        with self.assertRaisesRegex(PartShelfError, "SPICE"):
            self.prepare()

    def test_inheritance_is_flattened_with_local_overrides(self):
        base = sx.loads('(symbol "Base" (in_bom yes) (property "Value" "base") (symbol "Base_1_1" (pin passive line (number "1"))))')
        child = sx.loads('(symbol "Child" (extends "Base") (property "Value" "child"))')
        resolved = resolve_symbol(child, {"Base": base, "Child": child})
        self.assertEqual(properties(resolved)["Value"], "child")
        self.assertEqual(sx.value(sx.child(resolved, "symbol")[1]), "Child_1_1")
        self.assertFalse(sx.field(resolved, "extends"))

    def test_s_expression_roundtrip_preserves_unknown_fields(self):
        text = '(root (custom "a\\\"b\\\\c\\nd") (unknown yes (more 1.25)))'
        self.assertEqual(sx.loads(sx.dumps(sx.loads(text))), sx.loads(text))
        with self.assertRaises(sx.FormatError):
            sx.loads('(root (broken)')


class PackageTests(WorkspaceTest):
    def test_collection_deduplicates_and_roundtrips_deterministically(self):
        self.publish()
        self.catalog.publish(*self.prepare("Capacitor_100n", "test.capacitor"))
        data = export_package(self.catalog, name="My collection")
        self.assertEqual(data, export_package(self.catalog, name="My collection"))
        files = self.unpack(data)
        manifest, components = inspect_package(files)
        self.assertEqual(len(components), 2)
        referenced = sum(len(c["files"]) for c in manifest["components"])
        self.assertLess(len(manifest['objects']), referenced)
        other = Catalog(self.root / "other")
        self.assertEqual(import_package(other, files)["imported"], 2)
        self.assertEqual(import_package(other, files)["unchanged"], 2)
        self.assertEqual(export_package(other, name="My collection"), data)

    def test_tampering_is_detected_before_installation(self):
        self.publish()
        files = self.unpack(export_package(self.catalog))
        object_path = next(p for p in files if p.startswith(OBJECTS))
        files[object_path] += b"changed"
        with self.assertRaisesRegex(PartShelfError, "damaged asset"):
            import_package(Catalog(self.root / "other"), files)
        self.assertFalse((self.root / "other").exists())

    def test_same_revision_different_content_is_never_overwritten(self):
        self.publish(description="Original")
        other = Catalog(self.root / "other")
        other.publish(*self.prepare(description="Different"))
        before = other.read("test.resistor", 1)[2]
        with self.assertRaisesRegex(PartShelfError, "different content"):
            import_package(other, self.unpack(export_package(self.catalog)))
        self.assertEqual(other.read("test.resistor", 1)[2], before)

    def test_catalog_tampering_is_detected(self):
        self.publish()
        (self.catalog.root / "test.resistor/1/footprints/Part.kicad_mod").write_text("edited")
        with self.assertRaisesRegex(PartShelfError, "modified"):
            self.catalog.read("test.resistor", 1)

    def test_self_consistent_package_cannot_hide_external_asset(self):
        self.publish()
        archive = self.unpack(export_pcm(self.catalog))
        manifest, items = inspect_package(archive)
        meta, payload, _ = items[0]
        footprint = sx.loads(payload["footprints/Part.kicad_mod"].decode())
        sx.child(footprint, "model")[1] = sx.q("/external/model.step")
        payload["footprints/Part.kicad_mod"] = sx.encode(footprint)
        previous = manifest['components'][0]['files']['footprints/Part.kicad_mod']
        archive.pop(manifest['objects'].pop(previous))
        checksum = sha(payload['footprints/Part.kicad_mod'])
        archive[OBJECTS + checksum] = payload['footprints/Part.kicad_mod']
        manifest['objects'][checksum] = OBJECTS + checksum
        manifest['components'][0].update(digest=digest(payload), files=inventory(payload))
        archive[MANIFEST] = canonical(manifest)
        with self.assertRaisesRegex(PartShelfError, "outside its package"):
            inspect_package(archive)

    def test_zip_path_traversal_symlinks_and_case_conflicts(self):
        for name in ("../escape", "/absolute", "folder/../../escape"):
            with self.subTest(name=name):
                data = io.BytesIO()
                with zipfile.ZipFile(data, "w") as z:
                    z.writestr(name, b"bad")
                with self.assertRaises(PartShelfError):
                    self.unpack(data.getvalue())
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as z:
            item = zipfile.ZipInfo("link")
            item.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(item, "../target")
        with self.assertRaisesRegex(PartShelfError, "symlink"):
            self.unpack(data.getvalue())
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as z:
            z.writestr("Part.kicad_sym", "first")
            z.writestr("part.kicad_sym", "second")
        with self.assertRaisesRegex(PartShelfError, "conflicting"):
            self.unpack(data.getvalue())

    def test_catalog_writers_share_one_lock(self):
        self.publish()
        files = self.unpack(export_package(self.catalog))
        with self.catalog.write_lock():
            with self.assertRaisesRegex(PartShelfError, "Another import"):
                self.publish()
            with self.assertRaisesRegex(PartShelfError, "Another import"):
                import_package(self.catalog, files)


class ProjectTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.publish()
        projects.initialize(self.project, create_design=True)
        projects.install(self.project, self.catalog, "test.resistor", 1)

    def test_original_partshelf_registrations_verify_and_accept_updates(self):
        # Fixed registration text from the original package format. Do not
        # generate this fixture with table_entry(), which could hide a break.
        for table, extension in (("sym", ".kicad_sym"), ("fp", ".pretty")):
            (self.project / f"{table}-lib-table").write_text(
                f'({table}_lib_table (version 7) (lib (name "PS_test.resistor") '
                f'(type "KiCad") (uri "${{KIPRJMOD}}/.partshelf/kicad/test.resistor{extension}") '
                '(options "") (descr "Managed by PartShelf: test.resistor")))\n'
            )
        self.assertTrue(projects.verify(self.project)["ok"])
        self.publish(description="Updated through KiCad Component Packager")
        projects.install(self.project, self.catalog, "test.resistor", 2)
        result = projects.verify(self.project)
        self.assertTrue(result["ok"])
        self.assertEqual(result["components"][0]["revision"], 2)

    def test_installed_project_is_portable_and_restores_offline(self):
        self.assertTrue(projects.verify(self.project)["ok"])
        table = (self.project / "sym-lib-table").read_text()
        self.assertIn("${KIPRJMOD}", table)
        self.assertNotIn(str(self.root), table)
        moved = self.root / "moved"
        shutil.copytree(self.project, moved)
        self.assertTrue(projects.verify(moved)["ok"])
        (moved / ".partshelf/kicad/test.resistor.kicad_sym").unlink()
        (moved / "fp-lib-table").unlink()
        self.assertFalse(projects.verify(moved)["ok"])
        self.assertTrue(projects.restore(moved)["project"]["ok"])

    def test_revision_update_keeps_old_snapshot_and_placed_items(self):
        schematic = self.project / "project.kicad_sch"
        before = schematic.read_bytes()
        old_model = next((self.project / ".partshelf/components/test.resistor/1/models").iterdir())
        old_bytes = old_model.read_bytes()
        self.publish(description="New revision")
        plan = projects.plan(self.project, self.catalog, "test.resistor", 2)
        self.assertEqual(plan["action"], "update")
        self.assertIn("description", {c["field"] for c in plan["changes"]})
        projects.install(self.project, self.catalog, "test.resistor", 2, plan["state_digest"], plan["package_digest"])
        self.assertEqual(old_model.read_bytes(), old_bytes)
        self.assertEqual(schematic.read_bytes(), before)
        self.assertEqual(projects.verify(self.project)["archived_revisions"], 1)

    def test_unrelated_table_entries_survive(self):
        path = self.project / "sym-lib-table"
        tree = sx.loads(path.read_text())
        custom = sx.loads('(lib (name "MyExisting") (type "KiCad") (uri "${KIPRJMOD}/own.kicad_sym") (options "") (descr "Mine"))')
        tree.append(custom)
        path.write_bytes(sx.encode(tree))
        self.publish(description="Update")
        projects.install(self.project, self.catalog, "test.resistor", 2)
        self.assertIn(custom, sx.loads(path.read_text()))
        self.assertEqual(len(projects.verify(self.project)["unmanaged_libraries"]), 1)

    def test_nickname_collision_refuses_overwrite(self):
        other = self.root / "collision"
        other.mkdir()
        content = b'(sym_lib_table (lib (name "PS_test.resistor") (type "KiCad") (uri "owned.kicad_sym") (options "") (descr "")))'
        (other / "sym-lib-table").write_bytes(content)
        with self.assertRaisesRegex(PartShelfError, "already used"):
            projects.install(other, self.catalog, "test.resistor", 1)
        self.assertEqual((other / "sym-lib-table").read_bytes(), content)

    def test_modified_generated_asset_requires_explicit_repair(self):
        path = self.project / ".partshelf/kicad/test.resistor.kicad_sym"
        path.write_bytes(path.read_bytes() + b"; user edits\n")
        self.assertFalse(projects.verify(self.project)["ok"])
        with self.assertRaisesRegex(PartShelfError, "edited"):
            projects.restore(self.project, self.catalog)
        result = projects.restore(self.project, self.catalog, repair_modified=True)
        self.assertTrue(result["project"]["ok"])
        backup = self.project / result["backup"] / "before/.partshelf/kicad/test.resistor.kicad_sym"
        self.assertTrue(backup.read_bytes().endswith(b"; user edits\n"))

    def test_missing_snapshot_restored_only_from_matching_catalog(self):
        missing = self.project / ".partshelf/components/test.resistor/1/symbol.kicad_sym"
        missing.unlink()
        with self.assertRaisesRegex(PartShelfError, "Missing"):
            projects.restore(self.project)
        self.assertTrue(projects.restore(self.project, self.catalog)["project"]["ok"])

    def test_stale_update_preview_is_rejected(self):
        self.publish(description="New")
        plan = projects.plan(self.project, self.catalog, "test.resistor", 2)
        path = self.project / "sym-lib-table"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(PartShelfError, "changed after the preview"):
            projects.install(self.project, self.catalog, "test.resistor", 2, plan["state_digest"], plan["package_digest"])
        self.assertEqual(projects.verify(self.project)["components"][0]["revision"], 1)

    def test_failed_transaction_rolls_back_earlier_files(self):
        (self.project / "first.txt").write_bytes(b"original")
        original_write = projects.atomic_write
        def injected_failure(path, data):
            if path.name == "second.txt":
                raise OSError("Injected failure")
            return original_write(path, data)
        with mock.patch.object(projects, "atomic_write", side_effect=injected_failure):
            with self.assertRaisesRegex(OSError, "Injected"):
                projects.transaction(self.project, {"first.txt": b"replacement", "second.txt": b"new"})
        self.assertEqual((self.project / "first.txt").read_bytes(), b"original")
        self.assertFalse((self.project / "second.txt").exists())

    def test_project_zip_includes_hidden_snapshots_and_excludes_history(self):
        with zipfile.ZipFile(io.BytesIO(projects.export_project(self.project))) as z:
            self.assertIn("project/.partshelf/components/test.resistor/1/component.json", z.namelist())
            self.assertIn("project/partshelf.lock.json", z.namelist())
            self.assertFalse(any("/history/" in name for name in z.namelist()))


class ConnectorTests(WorkspaceTest):
    def collection_source(self, app):
        files = dict(self.source_files)
        for name, data in list(files.items()):
            if not name.endswith(".kicad_sym"):
                continue
            library = sx.loads(data.decode())
            for symbol in sx.children(library, "symbol"):
                title = sx.value(symbol[1])
                for key, value in {"Manufacturer": "Original " + title, "MPN": "MPN-" + title, "Category": "Original category", "Notes": "Original notes"}.items():
                    set_property(symbol, key, value)
            files[name] = sx.encode(library)
        return app.post("inspect", {"files": [{"name": name, "data": base64.b64encode(data).decode()} for name, data in files.items()]})

    def test_shared_collection_fields_preserve_individual_values_when_blank(self):
        app = Application(self.root, self.catalog.root)
        source = self.collection_source(app)
        result = app.post("import-library", {"session": source["session"], "prefix": "blank", "metadata": {"manufacturer": " ", "category": "", "notes": ""}})
        self.assertEqual(result["imported"], 3)
        for part in self.catalog.list():
            self.assertEqual(part["manufacturer"], "Original " + part["name"])
            self.assertEqual(part["mpn"], "MPN-" + part["name"])
            self.assertEqual(part["category"], "Original category")
            self.assertEqual(part["notes"], "Original notes")

    def test_collection_rules_and_shared_metadata_apply_to_the_correct_parts(self):
        app = Application(self.root, self.catalog.root)
        source = self.collection_source(app)
        chosen = source["symbols"][0]
        result = app.post("import-library", {"session": source["session"], "prefix": "shared", "metadata": {"manufacturer": "New vendor", "category": "My library", "notes": "Shared note", "website": "https://example.com/library"}, "part_numbers": {chosen["key"]: "EXTRACTED-123"}})
        self.assertEqual(result["imported"], 3)
        for part in self.catalog.list():
            self.assertEqual(part["manufacturer"], "New vendor")
            self.assertEqual(part["category"], "My library")
            self.assertEqual(part["notes"], "Shared note")
            self.assertEqual(part["website"], "https://example.com/library")
            self.assertEqual(part["mpn"], "EXTRACTED-123" if part["name"] == chosen["name"] else "MPN-" + part["name"])
        with self.assertRaises(PartShelfError):
            app.post("import-library", {"session": source["session"], "prefix": "invalid", "part_numbers": {"unknown": "123"}})
        self.assertFalse(any(part["id"].startswith("invalid.") for part in self.catalog.list()))

    def test_attached_pdf_and_website_survive_package_and_pcm_export(self):
        app = Application(self.root, self.catalog.root)
        source = self.collection_source(app)
        pdf = b"%PDF-1.4\n1 0 obj <</Type /Catalog>> endobj\n%%EOF\n"
        attached = app.post("attach-document", {"session": source["session"], "name": "datasheet.pdf", "data": base64.b64encode(pdf).decode()})
        app.post("import-library", {"session": source["session"], "prefix": "docs", "metadata": {"datasheet": attached["path"], "website": "https://example.com"}})
        archive = export_package(self.catalog)
        restored = Catalog(self.root / "restored")
        import_package(restored, self.unpack(archive))
        for part in restored.list():
            meta, assets, _ = restored.read(part["id"], part["revision"])
            self.assertEqual(assets[meta["datasheet"]], pdf)
            self.assertEqual(meta["website"], "https://example.com")
        pcm = export_pcm(restored, options={"identifier": "local.validation.parts", "name": "Validation parts"})
        self.assertEqual(pcm, export_pcm(restored, options={"identifier": "local.validation.parts", "name": "Validation parts"}))
        with zipfile.ZipFile(io.BytesIO(pcm)) as package:
            manifest = json.loads(package.read("metadata.json"))
            self.assertEqual(manifest["type"], "library")
            self.assertEqual(manifest["author"]["contact"], {})
            self.assertNotIn("download_url", manifest["versions"][0])
            symbols = next(name for name in package.namelist() if name.startswith("symbols/"))
            library = sx.loads(package.read(symbols).decode())
            self.assertEqual(len(sx.children(library, "symbol")), 3)
            for symbol in sx.children(library, "symbol"):
                values = properties(symbol)
                self.assertTrue(values["Footprint"].startswith("PCM_" + Path(symbols).stem + ":"))
                self.assertIn("${KICAD10_3RD_PARTY}/resources/local_validation_parts/documents/", values["Datasheet"])
            self.assertEqual(sum(name.startswith("resources/documents/") for name in package.namelist()), 1)
            self.assertEqual(sum(name.startswith("footprints/") for name in package.namelist()), 3)
        with self.assertRaises(PartShelfError):
            app.post("attach-document", {"session": source["session"], "name": "fake.pdf", "data": base64.b64encode(b"not a pdf").decode()})

    def test_node_package_manifest_does_not_mask_library_sources(self):
        self.assertFalse(is_package({"package.json": b'{"name":"vendor-tools","version":"1.0"}'}))
        app = Application(self.root, self.catalog.root)
        folder = self.root / "vendor"
        shutil.copytree(VENDOR, folder)
        (folder / "package.json").write_text('{"name":"vendor-tools"}')
        self.assertEqual(app.post("inspect", {"path": str(folder)})["kind"], "source")

    def test_download_credentials_do_not_enter_package_provenance(self):
        self.assertEqual(provenance_url("https://example.com/part.zip?token=private&expiry=123#fragment"), "https://example.com/part.zip")

    def test_public_url_rejects_private_networks_and_credentials(self):
        with mock.patch("partshelf.sources.socket.getaddrinfo", return_value=[(None, None, None, None, ("127.0.0.1", 443))]):
            with self.assertRaisesRegex(PartShelfError, "public internet"):
                public_url("https://private.example/file.zip")
        for url in ("http://example.com/file.zip", "https://user:pass@example.com/file.zip"):
            with self.subTest(url=url), self.assertRaises(PartShelfError):
                public_url(url)

    def test_product_page_explains_download_fallback(self):
        with mock.patch("partshelf.sources.download", return_value=(b"<!doctype html><html>login</html>", "text/html", "https://example.com/part")):
            with self.assertRaisesRegex(PartShelfError, "Ultra Librarian"):
                direct_source("https://example.com/part")

    def test_repository_import_records_exact_commit_and_source_hash(self):
        commit = "a" * 40
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as z:
            for path, data in self.source_files.items():
                z.writestr("repo-" + commit + "/" + path, data)
        responses = [(b'{"default_branch":"main"}', "application/json", ""), (json.dumps({"sha": commit}).encode(), "application/json", ""), (archive.getvalue(), "application/zip", "https://codeload.github.com/test/repo/zip/" + commit)]
        with mock.patch("partshelf.sources.public_url", return_value=__import__("urllib.parse", fromlist=["urlsplit"]).urlsplit("https://github.com/test/repo")), mock.patch("partshelf.sources.download", side_effect=responses) as downloader:
            files, origin = fetch_source("https://github.com/test/repo")
        self.assertEqual(files, self.source_files)
        self.assertEqual(origin["commit"], commit)
        self.assertEqual(origin["archive_sha256"], sha(archive.getvalue()))
        self.assertIn(commit, downloader.call_args.args[0])

    def test_upload_to_publish_to_project_application_flow(self):
        app = Application(self.root, self.catalog.root)
        uploaded = [{"name": name, "data": base64.b64encode(data).decode()} for name, data in self.source_files.items()]
        result = app.post("inspect", {"files": uploaded})
        choice = next(c for c in result["symbols"] if c["name"] == "Resistor_10k")
        with mock.patch.object(app, "previews", return_value={}):
            prepared = app.post("prepare", {"session": result["session"], "symbol": choice["key"], "footprint": choice["footprint"], "metadata": {"id": "my.resistor"}})
        app.post("publish", {"prepared": prepared["prepared"]})
        app.post("create-project", {"path": "my-board"})
        plan = app.post("plan", {"id": "my.resistor", "revision": 1})
        self.assertTrue(app.post("install", plan)["project"]["ok"])
        self.assertEqual(len(app.state()["catalog"]), 1)
        with self.assertRaisesRegex(PartShelfError, "workspace"):
            app.post("create-project", {"path": str(self.root.parent / "outside")})


if __name__ == "__main__":
    unittest.main()
