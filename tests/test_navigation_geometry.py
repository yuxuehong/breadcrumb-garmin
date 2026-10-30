"""Bounded watch geometry must preserve GPX gaps and its coordinate system."""
import json
import math
from pathlib import Path
import re
import sys
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'mac'))
from PIL import Image
from maps import (NAVIGATION_MAX_POINTS, NAVIGATION_MAX_TOLERANCE_M, PackError, Cancelled,
                  PROGRAM_VERSION, WORLD, generate_resources, navigation_geometry,
                  plan_route, project, tile_path)


def geometry_plan(segments, zoom=15):
    points = [point for segment in segments for point in segment]
    projected = [project(point) for point in points]
    ox = math.floor(min(p[0] for p in projected)*2**zoom)-1
    oy = math.floor(min(p[1] for p in projected)*2**zoom)-1
    return {'route': {'segments': segments}, 'zooms': [zoom], 'tiles': [[zoom, ox, oy]]}


def split_geometry(flat):
    segments = [[]]
    for x, y in zip(flat[::2], flat[1::2]):
        if x is None:
            assert y is None
            segments.append([])
        else:
            segments[-1].append((x, y))
    return segments


def distance_to_segment(point, start, end):
    dx, dy = end[0]-start[0], end[1]-start[1]
    px, py = point[0]-start[0], point[1]-start[1]
    length = dx*dx+dy*dy
    t = min(1, max(0, (px*dx+py*dy)/length)) if length else 0
    return math.hypot(px-t*dx, py-t*dy)


class NavigationGeometryTests(unittest.TestCase):
    def test_coordinates_match_base_zoom_origin_and_start(self):
        points = [[32, 118], [32.001, 118.002]]
        plan = geometry_plan([points])
        data = navigation_geometry(plan)
        z, ox, oy = plan['tiles'][0]
        expected = [value for point in points for value in
                    ((project(point)[0]*2**z-ox)*256, (project(point)[1]*2**z-oy)*256)]
        for actual, wanted in zip(data['points'], expected):
            self.assertAlmostEqual(actual, wanted, places=4)
        self.assertAlmostEqual(data['meters_per_pixel'],
                               WORLD*math.cos(math.radians(32.0005))/(256*2**z))

    def test_disconnected_segments_have_null_pair_and_all_endpoints(self):
        segments = [[[32, 118], [32.001, 118]], [[32.01, 118.02], [32.011, 118.02]]]
        data = navigation_geometry(geometry_plan(segments))
        self.assertEqual(data['points'][4:6], [None, None])
        self.assertEqual(data['point_count'], 4)
        self.assertEqual(data['array_entries'], 10)
        self.assertEqual(list(map(len, split_geometry(data['points']))), [2, 2])

    def test_closed_course_keeps_loop_and_closure(self):
        route = [[32, 118], [32, 118.002], [32.002, 118.002], [32.002, 118], [32, 118]]
        points = split_geometry(navigation_geometry(geometry_plan([route]))['points'])[0]
        self.assertEqual(len(points), 5)
        self.assertEqual(points[0], points[-1])
        self.assertEqual(len(set(points)), 4)

    def test_duplicate_and_singleton_segments_remain_valid(self):
        p = [32, 118]
        plan = geometry_plan([[p, p, p], [[32.001, 118.002]]])
        data = navigation_geometry(plan)
        self.assertEqual(data['point_count'], 2)
        self.assertEqual(list(map(len, split_geometry(data['points']))), [1, 1])
        self.assertEqual(data['source_point_count'], 4)

    def test_dense_course_is_bounded_with_explicit_adaptive_tolerance(self):
        route = [[32+0.0001*math.sin(i*math.pi/20), 118+i*0.000002] for i in range(6001)]
        data = navigation_geometry(geometry_plan([route]), max_points=128)
        self.assertLessEqual(data['array_entries'], 256)
        self.assertGreater(data['tolerance_m'], 3)
        self.assertLessEqual(data['tolerance_m'], NAVIGATION_MAX_TOLERANCE_M)
        self.assertEqual(data['source_point_count'], len(route))
        self.assertEqual(data['points'][:2], navigation_geometry(geometry_plan([[route[0], route[-1]]]))['points'][:2])

    def test_simplification_error_is_within_recorded_tolerance(self):
        route = [[32+0.00005*math.sin(i/7), 118+i*0.000002] for i in range(500)]
        plan = geometry_plan([route])
        data = navigation_geometry(plan)
        simplified = split_geometry(data['points'])[0]
        z, ox, oy = plan['tiles'][0]
        for point in route:
            original = ((project(point)[0]*2**z-ox)*256, (project(point)[1]*2**z-oy)*256)
            error = min(distance_to_segment(original, a, b) for a, b in zip(simplified, simplified[1:]))
            self.assertLessEqual(error*data['meters_per_pixel'], data['tolerance_m']+0.001)

    def test_too_many_segments_rejects_instead_of_bridging_or_truncating(self):
        segments = [[[32, 118], [32.001, 118.001]] for _ in range(700)]
        with self.assertRaisesRegex(PackError, '分段过多'):
            navigation_geometry(geometry_plan(segments))

    def test_default_hard_limit_includes_segment_separator_pairs(self):
        segments = [[[32, 118], [32.001, 118.001]] for _ in range(682)]
        data = navigation_geometry(geometry_plan(segments))
        self.assertLessEqual(data['array_entries'], NAVIGATION_MAX_POINTS*2)
        self.assertEqual(data['points'].count(None), 2*(len(segments)-1))

    def test_large_jagged_route_stops_at_shared_operation_budget(self):
        route = [[32+(0.0001 if i % 2 else 0), 118+i*0.000001] for i in range(10000)]
        plan = geometry_plan([route])
        with self.assertRaisesRegex(PackError, '计算上限'):
            navigation_geometry(plan, operation_budget=25000)
        self.assertEqual(plan['route']['segments'][0], route)

    def test_cancellation_interrupts_jagged_rdp_between_work_chunks(self):
        route = [[32+(0.0001 if i % 2 else 0), 118+i*0.000001] for i in range(10000)]
        plan = geometry_plan([route])

        class CancelDuringSimplification:
            # Projection and adjacent-duplicate checks require 40 chunks each;
            # cancellation after that must interrupt the RDP distance scans.
            checks = 0
            def is_set(self):
                self.checks += 1
                return self.checks > 90

        cancel = CancelDuringSimplification()
        with self.assertRaises(Cancelled):
            navigation_geometry(plan, cancel=cancel)
        self.assertEqual(cancel.checks, 91)

    def test_already_cancelled_generation_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            gpx = root/'route.gpx'
            gpx.write_text('<gpx><trk><trkseg><trkpt lat="32" lon="118"/>'
                           '<trkpt lat="32.001" lon="118.001"/></trkseg></trk></gpx>')
            plan = plan_route(gpx, 14, 150)
            for tile in plan['tiles']:
                path = root/'cache'/tile_path(tile)
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.new('RGB', (256, 256), 'white').save(path)
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(Cancelled):
                generate_resources(plan, root/'cache', root/'watch', cancel, lambda *args: None)
            self.assertFalse((root/'watch').exists())

    def test_generation_exposes_geometry_and_metadata_without_raw_points(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            gpx = root/'route.gpx'
            gpx.write_text('<gpx><trk><trkseg><trkpt lat="32" lon="118"/>'
                           '<trkpt lat="32.001" lon="118.001"/></trkseg></trk></gpx>')
            plan = plan_route(gpx, 14, 150)
            for tile in plan['tiles']:
                path = root/'cache'/tile_path(tile)
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.new('RGB', (256, 256), 'white').save(path)
            generate_resources(plan, root/'cache', root/'watch', threading.Event(), lambda *args: None)
            source = (root/'watch/source/MapIndex.mc').read_text()
            values = re.search(r'function navigationPoints\(\) \{ return \[(.*?)\]; \}', source).group(1)
            self.assertEqual(len(values.split(',')), 4)
            self.assertTrue(all(re.fullmatch(r'-?\d+\.\d{4}d', value) for value in values.split(',')))
            self.assertIn('const METERS_PER_PIXEL = ', source)
            metadata = json.loads((root/'watch/map-manifest.json').read_text())['navigation']
            self.assertEqual(metadata['point_count'], 2)
            self.assertEqual(metadata['tolerance_m'], 3)
            self.assertNotIn('points', metadata)
            self.assertEqual(PROGRAM_VERSION, 6)
            ns = {'iq': 'http://www.garmin.com/xml/connectiq'}
            for filename in ('manifest.xml', 'preview-manifest.xml'):
                app = ET.parse(root/'watch'/filename).getroot().find('iq:application', ns)
                self.assertEqual(app.get('minApiLevel'), '4.2.2')


if __name__ == '__main__':
    unittest.main()
