"""Synthetic FIT fixtures only: no recorded user locations or device access."""
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mac"))
import courses
from offline_pack import PackError, read_gpx


def crc16(data):
    # FIT's CRC-16/ARC, calculated independently of the production decoder.
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xa001 if crc & 1 else 0)
    return crc


def message(number, fields, endian="<", local=0):
    """Encode a definition and data message; fields are (number, type, bytes)."""
    definition = bytes([0x40 | local, 0, endian == ">"])
    definition += struct.pack(endian + "H", number) + bytes([len(fields)])
    definition += b"".join(bytes([field, len(value), kind]) for field, kind, value in fields)
    return definition + bytes([local]) + b"".join(value for _, _, value in fields)


def container(body, header_size=14):
    header = struct.pack("<BBHI4s", header_size, 0x20, 2184, len(body), b".FIT")
    if header_size == 14:
        header += struct.pack("<H", crc16(header))
    content = header + body
    return content + struct.pack("<H", crc16(content))


def file_id(kind=6):
    return message(0, [(0, 0, bytes([kind]))])


def course_name(name="合成赛道"):
    return message(31, [(5, 7, name.encode("utf-8") + b"\0")])


def record(lat, lon, elevation=120, endian="<", omit_lon=False, enhanced=None):
    def semicircle(value):
        return 0x7fffffff if value is None else round(value * (1 << 31) / 180)
    fields = [(0, 0x85, struct.pack(endian + "i", semicircle(lat))),
              (253, 0x86, struct.pack(endian + "I", 1000))]
    if not omit_lon:
        fields.append((1, 0x85, struct.pack(endian + "i", semicircle(lon))))
    if elevation is not None:
        fields.append((2, 0x84, struct.pack(endian + "H", round((elevation + 500) * 5))))
    if enhanced is not None:
        fields.append((78, 0x86, struct.pack(endian + "I", round((enhanced + 500) * 5))))
    return message(20, fields, endian=endian)


def fit_course(*records, name="合成赛道", kind=6, header_size=14):
    if not records:
        records = (record(30, 119), record(30.001, 119.002))
    return container(file_id(kind) + course_name(name) + b"".join(records), header_size)


class CourseTests(unittest.TestCase):
    def route(self, data, filename="race.fit"):
        output = courses.parse_course(data, filename)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "converted.gpx"
            path.write_bytes(output)
            return read_gpx(path)

    def test_fixture_crc_known_vector(self):
        self.assertEqual(crc16(b"123456789"), 0xbb3d)

    def test_fit_course_keeps_name_coordinates_and_elevation(self):
        route = self.route(fit_course(record(30, 119, 0), record(30.001, 119.002, None),
                                     name="山路 & <水库>"))
        self.assertEqual(route["name"], "山路 & <水库>")
        self.assertEqual(len(route["segments"]), 1)
        a, b = route["segments"][0]
        self.assertAlmostEqual(a[0], 30, places=6)
        self.assertAlmostEqual(b[1], 119.002, places=6)
        self.assertEqual(a[2], 0)
        self.assertIsNone(b[2])

    def test_zero_coordinates_are_not_missing(self):
        route = self.route(fit_course(record(0, 0), record(0, 0.001)))
        self.assertEqual(len(route["segments"][0]), 2)
        self.assertEqual(route["segments"][0][0][:2], [0, 0])

    def test_big_endian_records_and_12_byte_header(self):
        route = self.route(fit_course(record(-30, -119, endian=">"),
                                     record(-30.001, -119.002, endian=">"), header_size=12))
        self.assertAlmostEqual(route["segments"][0][0][0], -30, places=6)

    def test_optional_zero_header_crc_and_compressed_timestamp(self):
        # FIT's compressed form uses another local definition without the
        # timestamp field, following a normal Record containing the full time.
        fields = [(0, 0x85, struct.pack("<i", 0)),
                  (1, 0x85, struct.pack("<i", 0)),
                  (253, 0x86, struct.pack("<I", 1000))]
        initial = message(20, fields, local=2)
        definition = message(20, fields[:2], local=3)[:6 + 3 * 2]
        compressed = bytes([0x80 | (3 << 5) | (1001 & 31)]) + struct.pack("<ii", 0, 100000)
        data = bytearray(container(file_id() + course_name() + initial + definition + compressed))
        data[12:14] = b"\0\0"
        data[-2:] = struct.pack("<H", crc16(data[:-2]))
        route = self.route(bytes(data))
        self.assertEqual(len(route["segments"][0]), 2)
        self.assertAlmostEqual(route["segments"][0][1][1], 100000 * 180 / 2**31, places=7)

    def test_enhanced_altitude_preferred(self):
        route = self.route(fit_course(record(30, 119, 100, enhanced=123.4), record(30.1, 119.1)))
        self.assertEqual(route["segments"][0][0][2], 123.4)

    def test_missing_coordinate_records_break_segments(self):
        for missing in (record(None, None), record(None, 119), record(30, 119, omit_lon=True)):
            with self.subTest(missing=missing):
                route = self.route(fit_course(record(30, 119), record(30.001, 119.001),
                                             missing, record(31, 120), record(31.001, 120.001)))
                self.assertEqual([len(s) for s in route["segments"]], [2, 2])

    def test_timer_stop_breaks_segments(self):
        stop = message(21, [(0, 0, b"\x00"), (1, 0, b"\x01")])
        route = self.route(fit_course(record(30, 119), stop, record(31, 120)))
        self.assertEqual([len(s) for s in route["segments"]], [1, 1])

    def test_course_point_cues_are_not_used_as_geometry(self):
        cue = message(32, [(2, 0x85, struct.pack("<i", 0)),
                           (3, 0x85, struct.pack("<i", 0))])
        data = container(file_id() + course_name() + cue + cue)
        with self.assertRaisesRegex(PackError, "两个有效坐标"):
            courses.parse_course(data, "cue-only.fit")

    def test_missing_metadata_and_wrong_file_types_rejected(self):
        samples = [fit_course(kind=4), fit_course(kind=5),
                   container(course_name() + record(30, 119) + record(30.01, 119.01)),
                   container(file_id() + record(30, 119) + record(30.01, 119.01)),
                   container(file_id() + file_id() + course_name() + record(30, 119)),
                   container(file_id() + course_name() + course_name() + record(30, 119))]
        for data in samples:
            with self.subTest(data=data[:16]), self.assertRaises(PackError):
                courses.parse_course(data, "not-course.fit")

    def test_bad_crc_in_header_and_body_rejected(self):
        good = fit_course()
        for position in (12, len(good) - 3, len(good) - 1):
            data = bytearray(good)
            data[position] ^= 1
            with self.subTest(position=position), self.assertRaises(PackError):
                courses.parse_course(bytes(data), "broken.fit")

    def test_truncation_trailing_and_concatenated_data_rejected(self):
        good = fit_course()
        samples = [good[:cut] for cut in (1, 11, 12, 13, 14, len(good) - 3, len(good) - 1)]
        samples += [good + b"\0", good + good]
        for data in samples:
            with self.subTest(length=len(data)), self.assertRaises(PackError):
                courses.parse_course(data, "broken.fit")

    def test_invalid_latitude_and_array_coordinate_rejected(self):
        array = message(20, [(0, 0x85, struct.pack("<ii", 0, 0)),
                             (1, 0x85, struct.pack("<i", 0))])
        for first in (record(90, 0), record(-86, 0), array):
            with self.subTest(first=first), self.assertRaises(PackError):
                courses.parse_course(fit_course(first, record(30, 119)), "invalid.fit")

    def test_invalid_definition_is_rejected_even_with_valid_crc(self):
        bad_definition = message(20, [(0, 0x85, b"\x00"), (1, 0x85, b"\0" * 4)])
        with self.assertRaises(PackError):
            courses.parse_course(fit_course(bad_definition, record(30, 119)), "invalid.fit")

    def test_invalid_message_length_with_valid_crc_is_a_user_error(self):
        # A malformed definition can make fitdecode assert its body boundary
        # even when the file-level size and both checksums are valid.
        data = bytearray(fit_course())
        data[32] = 80
        data[-2:] = struct.pack("<H", crc16(data[:-2]))
        with self.assertRaisesRegex(PackError, "FIT 校验失败"):
            courses.parse_course(bytes(data), "malformed.fit")

    def test_at_least_two_valid_points_required(self):
        for points in ((record(30, 119),), (record(None, None), record(30, 119))):
            with self.subTest(points=points), self.assertRaises(PackError):
                courses.parse_course(fit_course(*points), "short.fit")

    def test_name_controls_removed_and_blank_name_uses_filename(self):
        route = self.route(fit_course(name="A\x01B\u202e &<山>"))
        self.assertEqual(route["name"], "AB &<山>")
        route = self.route(fit_course(name=""), "../../比赛 & 山路.FIT")
        self.assertEqual(route["name"], "比赛 & 山路")

    def test_gpx_is_validated_and_preserved_exactly(self):
        text = '<?xml version="1.0"?><gpx><trk><name>路线</name><trkseg>\n<trkpt lat="0" lon="0"/><trkpt lat="0" lon="0.1"/></trkseg></trk></gpx>'
        for data in (text.encode("utf-8"), text.encode("utf-16")):
            self.assertEqual(courses.parse_course(data, "路线.GPX"), data)

    def test_invalid_gpx_and_entities_rejected(self):
        samples = ['<gpx/>', '<other/>',
                   '<gpx><rte><rtept lat="nan" lon="0"/><rtept lat="0" lon="0"/></rte></gpx>',
                   '<!DOCTYPE gpx [<!ENTITY value "expanded">]><gpx/>']
        for text in samples:
            for encoding in ("utf-8", "utf-16"):
                with self.subTest(text=text, encoding=encoding), self.assertRaises(PackError):
                    courses.parse_course(text.encode(encoding), "invalid.gpx")

    def test_input_size_and_point_budgets(self):
        with mock.patch.object(courses, "MAX_COURSE_BYTES", 64), self.assertRaises(PackError):
            courses.parse_course(fit_course(), "large.fit")
        with mock.patch.object(courses, "MAX_COURSE_POINTS", 2), self.assertRaises(PackError):
            courses.parse_course(fit_course(record(0, 0), record(0, 0.1), record(0, 0.2)), "large.fit")
        data = b'<gpx><rte><rtept lat="0" lon="0"/><rtept lat="0" lon="1"/><rtept lat="0" lon="2"/></rte></gpx>'
        with mock.patch.object(courses, "MAX_COURSE_POINTS", 2), self.assertRaises(PackError):
            courses.parse_course(data, "large.gpx")

    def test_empty_unknown_extension_and_wrong_magic_rejected(self):
        for data, filename in ((b"", "empty.fit"), (b"hello", "wrong.fit"),
                               (fit_course(), "route.txt")):
            with self.subTest(filename=filename), self.assertRaises(PackError):
                courses.parse_course(data, filename)


if __name__ == "__main__":
    unittest.main()
