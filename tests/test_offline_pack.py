import copy
import contextlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from tools.offline_pack import (PackError, build_package, checked_png, corridor_tiles,
                               load_plan, main, make_plan, read_gpx, tile_path,
                               verify_package, write_json)


def png(size=64):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\0" + b"\x80\x80\x80" * size) * size)) + chunk(b"IEND", b""))


class OfflinePackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.gpx = self.root / "route.gpx"
        self.gpx.write_text('<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><name>测试路线</name><trkseg>'
                            '<trkpt lat="29.60" lon="119.00"><ele>120</ele></trkpt>'
                            '<trkpt lat="29.61" lon="119.01"/></trkseg></trk></gpx>', encoding="utf-8")

    def plan(self):
        return make_plan(self.gpx, [14], 100)

    def tiles(self, plan):
        root = self.root / "source"
        for tile in plan["tiles"]:
            path = root / tile_path(tile)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(png())
        return root

    def test_preserves_name_elevations_and_missing_height(self):
        route = read_gpx(self.gpx)
        self.assertEqual(route["name"], "测试路线")
        self.assertEqual(route["segments"][0][0], [29.6, 119.0, 120.0])
        self.assertIsNone(route["segments"][0][1][2])

    def test_supports_route_without_namespace(self):
        self.gpx.write_text('<gpx><rte><rtept lat="1" lon="2"/><rtept lat="1.1" lon="2"/></rte></gpx>')
        self.assertEqual(len(read_gpx(self.gpx)["segments"][0]), 2)

    def test_rejects_nan_and_entity_document(self):
        for text in ('<gpx><rte><rtept lat="nan" lon="0"/></rte></gpx>',
                     '<!DOCTYPE gpx [<!ENTITY x "x">]><gpx/>'):
            self.gpx.write_text(text)
            with self.assertRaises(PackError):
                read_gpx(self.gpx)

    def test_utf16_does_not_bypass_entity_rejection(self):
        self.gpx.write_bytes('<!DOCTYPE gpx [<!ENTITY x "x">]><gpx/>'.encode("utf-16"))
        with self.assertRaises(PackError):
            read_gpx(self.gpx)

    def test_sparse_segment_covers_middle_not_only_endpoints(self):
        route = {"segments": [[[0.1, 0.1, None], [0.1, 2.0, None]]]}
        tiles = corridor_tiles(route, [10], 0, 100)
        self.assertIn([10, 514, 511], tiles)

    def test_disconnected_segments_do_not_create_a_false_bridge(self):
        route = {"segments": [[[0.1, 0.1, None]], [[0.1, 2.0, None]]]}
        tiles = corridor_tiles(route, [10], 0, 100)
        self.assertNotIn([10, 514, 511], tiles)

    def test_more_buffer_never_removes_tiles(self):
        route = read_gpx(self.gpx)
        small = set(map(tuple, corridor_tiles(route, [14], 0, 100)))
        large = set(map(tuple, corridor_tiles(route, [14], 500, 100)))
        self.assertLessEqual(small, large)

    def test_budget_rejects_instead_of_truncating(self):
        with self.assertRaises(PackError):
            make_plan(self.gpx, [16], 1000, 1)

    def test_complete_package_and_tamper_detection(self):
        plan = self.plan()
        source = self.tiles(plan)
        target = self.root / "package"
        build_package(plan, source, target, "Synthetic test imagery; not real map data", 1048576)
        self.assertEqual(verify_package(target), len(plan["tiles"]))
        self.assertFalse(json.loads((target / "manifest.json").read_text())["installable_on_watch"])
        path = target / "tiles" / tile_path(plan["tiles"][0])
        path.write_bytes(png(128))
        with self.assertRaises(PackError):
            verify_package(target)

    def test_missing_tile_never_publishes_partial_package(self):
        plan = self.plan()
        source = self.tiles(plan)
        (source / tile_path(plan["tiles"][0])).unlink()
        target = self.root / "package"
        with self.assertRaises(PackError):
            build_package(plan, source, target, "Test", 1048576)
        self.assertFalse(target.exists())

    def test_corrupt_png_and_oversize_leave_no_package(self):
        plan = self.plan()
        source = self.tiles(plan)
        target = self.root / "package"
        with self.assertRaises(PackError):
            build_package(plan, source, target, "Test", 1)
        self.assertFalse(target.exists())
        (source / tile_path(plan["tiles"][0])).write_text("<html>404</html>")
        with self.assertRaises(PackError):
            build_package(plan, source, target, "Test", 1048576)
        self.assertFalse(target.exists())
        self.assertFalse(list(self.root.glob(".breadcrumb-*")))

    def test_existing_package_is_preserved(self):
        target = self.root / "package"
        target.mkdir()
        (target / "keep").write_text("existing")
        with self.assertRaises(PackError):
            build_package(self.plan(), self.root, target, "Test", 1048576)
        self.assertEqual((target / "keep").read_text(), "existing")

    def test_plan_cannot_remove_required_tiles(self):
        plan = copy.deepcopy(self.plan())
        plan["tiles"] = plan["tiles"][1:]
        path = self.root / "plan.json"
        write_json(path, plan)
        with self.assertRaises(PackError):
            load_plan(path)

    def test_png_checksum_detects_damage(self):
        path = self.root / "damaged.png"
        data = bytearray(png())
        data[20] ^= 1
        path.write_bytes(data)
        with self.assertRaises(PackError):
            checked_png(path)

    def test_cli_plan_does_not_overwrite(self):
        output = self.root / "plan.json"
        args = ["plan", str(self.gpx), "--output", str(output)]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(args), 0)
        original = output.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(args), 1)
        self.assertEqual(output.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
