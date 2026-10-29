import base64
import hashlib
import io
import json
import math
import re
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import xml.etree.ElementTree as ET

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'mac'))
from PIL import Image
from maps import (PackError, Cancelled, decode_tile, download_tiles, generate_resources,
                  plan_route, tile_path, tile_image, APP_ID, PREVIEW_APP_ID, PROGRAM_VERSION, preview_bounds,
                  project, make_plan)
from device import validate_program, install
from server import Desktop

GPX=b'<gpx><trk><name>test</name><trkseg><trkpt lat="30" lon="119"/><trkpt lat="30.001" lon="119.002"/></trkseg></trk></gpx>'


class Response(io.BytesIO):
    def geturl(self): return 'https://a.tile.opentopomap.org/14/1/1.png'


class MacWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.gpx=self.root/'race.gpx';self.gpx.write_bytes(GPX)
        self.plan=plan_route(self.gpx,14,150)
        image=Image.new('RGB',(256,256),'#c9d9b4')
        data=io.BytesIO();image.save(data,format='PNG');self.png=data.getvalue()
        self.cancel=threading.Event()

    def seed(self):
        for tile in self.plan['tiles']:
            file=self.root/'cache'/tile_path(tile);file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(self.png)

    def route_file(self, points, name='geometry'):
        path=self.root/(name+'.gpx')
        path.write_text('<gpx><trk><trkseg>'+''.join(
            '<trkpt lat="%s" lon="%s"/>'%(lat,lon) for lat,lon in points
        )+'</trkseg></trk></gpx>')
        return path

    def test_cached_download_is_offline(self):
        self.seed()
        def forbidden(*args,**kwargs): self.fail('network called for cached maps')
        self.assertEqual(download_tiles(self.plan,self.root/'cache',self.cancel,lambda *a:None,forbidden),0)

    def test_cancel_preserves_cached_maps(self):
        self.seed();self.cancel.set()
        with self.assertRaises(Cancelled): download_tiles(self.plan,self.root/'cache',self.cancel,lambda *a:None)
        self.assertEqual(len(list((self.root/'cache').rglob('*.png'))),len(self.plan['tiles']))

    def test_download_validates_image_before_cache(self):
        one={**self.plan,'tiles':self.plan['tiles'][:1]}
        with self.assertRaises(PackError):
            download_tiles(one,self.root/'cache',self.cancel,lambda *a:None,lambda *a,**kw:Response(b'<html>Error</html>'))
        self.assertFalse((self.root/'cache'/tile_path(one['tiles'][0])).exists())

    def test_rate_limit_stops_without_retry_storm(self):
        calls=[]
        def limited(*a,**kw):
            calls.append(1);raise urllib.error.HTTPError('https://example.test',429,'slow',{},None)
        with self.assertRaises(PackError): download_tiles(self.plan,self.root/'cache',self.cancel,lambda *a:None,limited)
        self.assertEqual(len(calls),1)

    def test_valid_download_is_atomic_and_resumable(self):
        one={**self.plan,'tiles':self.plan['tiles'][:1]}
        download_tiles(one,self.root/'cache',self.cancel,lambda *a:None,lambda *a,**kw:Response(self.png))
        self.assertEqual((self.root/'cache'/tile_path(one['tiles'][0])).read_bytes(),self.png)
        self.assertFalse(list((self.root/'cache').rglob('*.partial')))

    def test_decoding_rejects_wrong_dimensions(self):
        data=io.BytesIO();Image.new('RGB',(512,512)).save(data,format='PNG')
        with self.assertRaises(PackError):decode_tile(data.getvalue())

    def test_generated_maps_cover_every_planned_tile(self):
        self.seed();generate_resources(self.plan,self.root/'cache',self.root/'watch',self.cancel,lambda *a:None)
        manifest=json.loads((self.root/'watch/map-manifest.json').read_text())
        self.assertEqual([r['tile'] for r in manifest['tiles']],self.plan['tiles'])
        self.assertEqual(len(list((self.root/'watch/resources').glob('tile_*.png'))),len(self.plan['tiles']))
        source=(self.root/'watch/source/MapIndex.mc').read_text()
        for i in range(len(self.plan['tiles'])):self.assertIn('Rez.Drawables.Tile%d'%i,source)
        self.assertIn(APP_ID,(self.root/'watch/manifest.xml').read_text())

    def test_original_gpx_is_not_modified(self):
        self.seed();tile_image(self.plan,self.root/'cache',self.plan['tiles'][0])
        self.assertEqual(self.gpx.read_bytes(),GPX)

    def test_standalone_preview_has_distinct_identity_and_no_recording_or_gps_permission(self):
        self.seed();generate_resources(self.plan,self.root/'cache',self.root/'watch',self.cancel,lambda *a:None)
        ns={'iq':'http://www.garmin.com/xml/connectiq'}
        root=ET.parse(self.root/'watch/preview-manifest.xml').getroot()
        app=root.find('iq:application',ns)
        self.assertEqual(app.get('id'),PREVIEW_APP_ID)
        self.assertNotEqual(app.get('id'),APP_ID)
        self.assertEqual(app.get('type'),'watch-app')
        self.assertEqual(app.get('entry'),'TrailViewerApp')
        self.assertEqual(root.findall('.//iq:uses-permission',ns),[])

    def test_every_zoom_uses_its_own_downloaded_map_tiles(self):
        # Different map colors at each level detect accidental downsampling of
        # the detailed corridor instead of reading genuine overview coverage.
        colors={z:(20+z,150,40) for z in {t[0] for t in self.plan['tiles']}}
        for tile in self.plan['tiles']:
            path=self.root/'cache'/tile_path(tile);path.parent.mkdir(parents=True,exist_ok=True)
            Image.new('RGB',(256,256),colors[tile[0]]).save(path)
        with patch('urllib.request.urlopen',side_effect=AssertionError('preview must be offline')):
            generate_resources(self.plan,self.root/'cache',self.root/'watch',self.cancel,lambda *a:None)
        manifest=json.loads((self.root/'watch/map-manifest.json').read_text())
        base=self.plan['zooms'][0]
        self.assertLess(manifest['preview_min_zoom'],base)
        self.assertEqual([r['tile'] for r in manifest['tiles']],self.plan['tiles'])
        for index,record in enumerate(manifest['tiles']):
            image=decode_tile((self.root/'watch/resources'/('tile_%d.png'%index)).read_bytes())
            histogram=dict((color,count) for count,color in image.getcolors(256*256))
            self.assertGreater(histogram.get(colors[record['tile'][0]],0),256*256//2)
        self.assertFalse(list((self.root/'watch/resources').glob('Preview*.png')))

    def test_each_zoom_has_complete_rectangle_without_holes(self):
        path=self.route_file([(30,119),(30.02,119.03),(30.01,119.06)])
        plan=plan_route(path,14,300)
        self.assertEqual(plan['coverage_version'],1)
        levels=set(range(plan['preview_min_zoom'],15))
        self.assertEqual({int(z) for z in plan['coverage']},levels)
        self.assertEqual({t[0] for t in plan['tiles']},levels)
        self.assertEqual(len(plan['tiles']),len({tuple(t) for t in plan['tiles']}))
        for z in levels:
            x0,y0,x1,y1=plan['coverage'][str(z)]
            expected={(x,y) for x in range(x0,x1+1) for y in range(y0,y1+1)}
            actual={(x,y) for level,x,y in plan['tiles'] if level==z}
            self.assertEqual(actual,expected)

    def test_260_pixel_viewport_fits_at_every_route_point_and_zoom(self):
        points=[(30,119),(30.02,119.03),(30.01,119.06)]
        path=self.route_file(points)
        for zoom in (14,15):
            for buffer in (150,1000):
                plan=plan_route(path,zoom,buffer)
                for level,bounds in plan['coverage'].items():
                    z=int(level);factor=256*2**z
                    left,top=bounds[0]*256,bounds[1]*256
                    right,bottom=(bounds[2]+1)*256,(bounds[3]+1)*256
                    for point in points:
                        px,py=project(point);px*=factor;py*=factor
                        with self.subTest(zoom=zoom,buffer=buffer,level=z,point=point):
                            # 130 pixels for the biggest 255 screen plus margin.
                            self.assertLessEqual(left,px-132)
                            self.assertLessEqual(top,py-132)
                            self.assertGreaterEqual(right,px+132)
                            self.assertGreaterEqual(bottom,py+132)

    def test_diagonal_route_fits_inside_small_round_watch(self):
        path=self.route_file([(30,119),(30.14,119.14)])
        plan=plan_route(path,14,150)
        bounds,minimum=preview_bounds(plan)
        self.assertEqual(minimum,plan['preview_min_zoom'])
        diagonal=math.hypot(bounds[2]-bounds[0],bounds[3]-bounds[1])
        visible=diagonal*2**(minimum-14)
        self.assertLessEqual(visible,194)
        self.assertGreater(visible*2,194)

    def test_short_route_has_at_least_one_overview_level(self):
        _,minimum=preview_bounds(self.plan)
        self.assertLessEqual(minimum,self.plan['zooms'][0]-1)

    def test_total_tile_limit_includes_real_overview_coverage(self):
        path=self.route_file([(30,119),(30.11,119.11)])
        original=path.read_bytes()
        with patch('maps.MAX_TILES',4096):
            large=plan_route(path,15,150)
        self.assertLessEqual(sum(t[0]==15 for t in large['tiles']),256)
        self.assertGreater(len(large['tiles']),256)
        with self.assertRaises(PackError):plan_route(path,15,150)
        self.assertEqual(path.read_bytes(),original)

    def test_missing_cache_at_any_zoom_rejects_map_generation(self):
        self.seed()
        for level in sorted({t[0] for t in self.plan['tiles']}):
            tile=next(t for t in self.plan['tiles'] if t[0]==level)
            missing=self.root/'cache'/tile_path(tile);missing.unlink()
            output=self.root/('missing-%d'%level)
            with self.subTest(level=level):
                with self.assertRaises(PackError):
                    generate_resources(self.plan,self.root/'cache',output,self.cancel,lambda *a:None)
                self.assertFalse((output/'map-manifest.json').exists())
            missing.write_bytes(self.png)

    def test_incomplete_tile_list_cannot_generate_blank_quadrants(self):
        self.seed()
        incomplete={**self.plan,'tiles':self.plan['tiles'][1:]}
        with self.assertRaises(PackError):
            generate_resources(incomplete,self.root/'cache',self.root/'incomplete',self.cancel,lambda *a:None)

    def test_missing_whole_zoom_level_is_rejected_even_if_tiles_match_coverage(self):
        self.seed()
        incomplete=json.loads(json.dumps(self.plan))
        level=incomplete['preview_min_zoom']
        incomplete['coverage'].pop(str(level))
        incomplete['tiles']=[t for t in incomplete['tiles'] if t[0]!=level]
        output=self.root/'missing-level'
        with self.assertRaises(PackError):
            generate_resources(incomplete,self.root/'cache',output,self.cancel,lambda *a:None)
        self.assertFalse(output.exists())

    def test_oversize_coverage_is_rejected_before_expanding_tile_rectangle(self):
        self.seed()
        damaged=json.loads(json.dumps(self.plan))
        level=damaged['preview_min_zoom']
        # Legal world coordinates, but tens of millions of tiles. A guard
        # makes a regression fail safely instead of allocating that rectangle.
        damaged['coverage'][str(level)]=[0,0,2**level-1,2**level-1]
        def guarded_range(*args):
            result=range(*args)
            if len(result)>256:
                raise AssertionError('oversize tile rectangle expanded before budget check')
            return result
        output=self.root/'oversize-coverage'
        with patch('maps.range',side_effect=guarded_range,create=True):
            with self.assertRaises(PackError):
                generate_resources(damaged,self.root/'cache',output,self.cancel,lambda *a:None)
        self.assertFalse(output.exists())

    def test_coverage_narrower_than_260_pixels_is_rejected(self):
        self.seed()
        for axis in (0,1):
            damaged=json.loads(json.dumps(self.plan))
            level=damaged['preview_min_zoom']
            bounds=damaged['coverage'][str(level)]
            bounds[axis+2]=bounds[axis]  # One tile is only 256 pixels wide/high.
            damaged['tiles']=[t for t in damaged['tiles']
                              if t[0]!=level or t[axis+1]==bounds[axis]]
            output=self.root/('narrow-%d'%axis)
            with self.subTest(axis=axis):
                with self.assertRaises(PackError):
                    generate_resources(damaged,self.root/'cache',output,self.cancel,lambda *a:None)
                self.assertFalse(output.exists())

    def test_generated_extents_match_each_zoom_in_base_pixel_coordinates(self):
        self.seed();generate_resources(self.plan,self.root/'cache',self.root/'watch',self.cancel,lambda *a:None)
        source=(self.root/'watch/source/MapIndex.mc').read_text()
        ox=int(re.search(r'const ORIGIN_X = (\d+)',source).group(1))
        oy=int(re.search(r'const ORIGIN_Y = (\d+)',source).group(1))
        extents={z:list(map(int,values.split(','))) for z,values in re.findall(
            r'if \(zoom == (\d+)\) \{ return \[([-\d,]+)\]; \}',source)}
        self.assertEqual(set(extents),set(self.plan['coverage']))
        for level,(x0,y0,x1,y1) in self.plan['coverage'].items():
            scale=2**(self.plan['zooms'][0]-int(level))
            self.assertEqual(extents[level],[(x0*scale-ox)*256,(y0*scale-oy)*256,
                                             ((x1+1)*scale-ox)*256,((y1+1)*scale-oy)*256])

    def test_saved_camera_identity_changes_when_map_grid_changes(self):
        identities=[]
        for index,zoom in enumerate((14,14,15)):
            self.plan=plan_route(self.gpx,zoom,150)
            self.seed()
            output=self.root/('watch%d'%index)
            generate_resources(self.plan,self.root/'cache',output,self.cancel,lambda *a:None)
            source=(output/'source/MapIndex.mc').read_text()
            identities.append(re.search(r'const ROUTE_ID = "([a-f0-9]+)"',source).group(1))
        self.assertEqual(identities[0],identities[1])
        self.assertNotEqual(identities[0],identities[2])

    def test_cancelled_preview_does_not_damage_cached_tiles(self):
        self.seed();before={str(p):p.read_bytes() for p in (self.root/'cache').rglob('*.png')}
        def stop(*args):self.cancel.set()
        with self.assertRaises(Cancelled):
            generate_resources(self.plan,self.root/'cache',self.root/'watch',self.cancel,stop)
        self.assertEqual(before,{str(p):p.read_bytes() for p in (self.root/'cache').rglob('*.png')})

    def test_app_rejects_new_import_while_busy(self):
        desktop=Desktop(self.root/'desktop');desktop.job['active']=True
        with self.assertRaises(PackError):desktop.import_route({'gpx':base64.b64encode(GPX).decode()})
        self.assertEqual(list(desktop.projects.iterdir()),[])

    def test_changed_options_keep_old_race(self):
        desktop=Desktop(self.root/'desktop')
        result=desktop.import_route({'gpx':base64.b64encode(GPX).decode(),'zoom':14,'buffer':150})
        first=result['id'];desktop.update_options({'zoom':15,'buffer':300})
        self.assertNotEqual(first,desktop.current)
        self.assertEqual((desktop.projects/first/'route.gpx').read_bytes(),GPX)

    def test_project_path_traversal_rejected(self):
        desktop=Desktop(self.root/'desktop')
        with self.assertRaises(PackError):desktop.select('../outside')

    def program(self):
        folder=self.root/'program';folder.mkdir()
        data=b'test-program'*100
        preview=b'test-preview'*100
        (folder/'BCTRAIL.prg').write_bytes(data)
        (folder/'BCPREVW.prg').write_bytes(preview)
        (folder/'program.json').write_text(json.dumps({'device':'fr255','app_id':APP_ID,'program_version':PROGRAM_VERSION,
            'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'total_bytes':len(data)+len(preview),
            'preview':{'filename':'BCPREVW.prg','app_id':PREVIEW_APP_ID,'bytes':len(preview),
                       'sha256':hashlib.sha256(preview).hexdigest()}}))
        return folder

    def test_wrong_device_or_tampered_program_blocks_install(self):
        folder=self.program()
        with self.assertRaises(PackError): validate_program(folder,'fr255m')
        (folder/'BCTRAIL.prg').write_bytes(b'changed')
        with self.assertRaises(PackError):validate_program(folder,'fr255')

    def test_old_map_program_requires_rebuild_before_install(self):
        folder=self.program()
        metadata=json.loads((folder/'program.json').read_text())
        metadata.pop('program_version')
        (folder/'program.json').write_text(json.dumps(metadata))
        with self.assertRaises(PackError):validate_program(folder,'fr255')

    def test_saved_race_with_old_program_remains_available_for_upgrade(self):
        desktop=Desktop(self.root/'desktop')
        result=desktop.import_route({'gpx':base64.b64encode(GPX).decode(),'zoom':14,'buffer':150})
        folder=desktop.projects/result['id'];(folder/'watch').mkdir()
        (folder/'watch/program.json').write_text('{}')
        desktop.select(result['id'])
        self.assertFalse(desktop.summary()['ready'])
        with self.assertRaises(PackError):desktop.start('install')
        self.assertEqual((folder/'route.gpx').read_bytes(),GPX)

    def test_select_upgrades_legacy_coverage_without_losing_saved_race(self):
        desktop=Desktop(self.root/'desktop')
        result=desktop.import_route({'gpx':base64.b64encode(GPX).decode(),'zoom':14,'buffer':150})
        folder=desktop.projects/result['id']
        old=make_plan(folder/'route.gpx',[14],150,256)
        old['route']['name']='Saved race name'
        (folder/'plan.json').write_text(json.dumps(old))
        watch=folder/'watch';watch.mkdir()
        (watch/'program.json').write_text('{"program_version":4}')
        (watch/'BCTRAIL.prg').write_bytes(b'previous-build')
        cache=desktop.cache/tile_path(old['tiles'][0]);cache.parent.mkdir(parents=True)
        cache.write_bytes(self.png)
        desktop.current=None
        desktop.select(result['id'])
        upgraded=json.loads((folder/'plan.json').read_text())
        self.assertEqual(upgraded['coverage_version'],1)
        self.assertEqual(upgraded['zooms'],[14])
        self.assertEqual(upgraded['buffer_m'],150)
        self.assertEqual(upgraded['route']['name'],'Saved race name')
        self.assertGreater(len(upgraded['tiles']),len(old['tiles']))
        self.assertEqual((folder/'route.gpx').read_bytes(),GPX)
        self.assertEqual((watch/'BCTRAIL.prg').read_bytes(),b'previous-build')
        self.assertEqual(cache.read_bytes(),self.png)
        self.assertFalse(desktop.summary()['ready'])
        with self.assertRaises(PackError):desktop.start('install')

    def test_start_upgrades_legacy_plan_before_download_worker(self):
        desktop=Desktop(self.root/'desktop')
        result=desktop.import_route({'gpx':base64.b64encode(GPX).decode(),'zoom':14,'buffer':150})
        folder=desktop.projects/result['id']
        old=make_plan(folder/'route.gpx',[14],150,256)
        (folder/'plan.json').write_text(json.dumps(old))
        with patch('server.threading.Thread') as worker:
            desktop.start('download')
        worker.return_value.start.assert_called_once()
        self.assertEqual(json.loads((folder/'plan.json').read_text())['coverage_version'],1)

    def test_oversize_legacy_upgrade_preserves_original_plan_and_build(self):
        desktop=Desktop(self.root/'desktop')
        identity='a'*32;folder=desktop.projects/identity;folder.mkdir()
        route=self.route_file([(30,119),(30.11,119.11)])
        (folder/'route.gpx').write_bytes(route.read_bytes())
        old=make_plan(folder/'route.gpx',[15],150,256)
        original=json.dumps(old).encode();(folder/'plan.json').write_bytes(original)
        watch=folder/'watch';watch.mkdir();(watch/'BCTRAIL.prg').write_bytes(b'previous-build')
        with self.assertRaises(PackError):desktop.select(identity)
        self.assertEqual((folder/'plan.json').read_bytes(),original)
        self.assertEqual((folder/'route.gpx').read_bytes(),route.read_bytes())
        self.assertEqual((watch/'BCTRAIL.prg').read_bytes(),b'previous-build')

    def test_volume_install_backs_up_and_leaves_other_apps(self):
        folder=self.program();apps=self.root/'GARMIN/APPS';apps.mkdir(parents=True)
        (apps/'BCTRAIL.PRG').write_bytes(b'old-map');(apps/'OTHER.PRG').write_bytes(b'other')
        with patch('device.detect',return_value={'device':'fr255','transport':'volume','apps':str(apps)}):
            result=install(folder,self.root/'backups')
        self.assertEqual((apps/'BCTRAIL.PRG').read_bytes(),(folder/'BCTRAIL.prg').read_bytes())
        self.assertEqual((Path(result['backup'])/'previous.prg').read_bytes(),b'old-map')
        self.assertEqual((apps/'OTHER.PRG').read_bytes(),b'other')
        self.assertFalse(result['hardware_verified'])

    def test_insufficient_space_preserves_previous_map(self):
        folder=self.program();apps=self.root/'GARMIN/APPS';apps.mkdir(parents=True)
        (apps/'BCTRAIL.PRG').write_bytes(b'old-map')
        with patch('device.detect',return_value={'device':'fr255','transport':'volume','apps':str(apps)}),patch('device.shutil.disk_usage') as usage:
            usage.return_value.free=1
            with self.assertRaises(PackError):install(folder,self.root/'backups')
        self.assertEqual((apps/'BCTRAIL.PRG').read_bytes(),b'old-map')


if __name__=='__main__':unittest.main()
