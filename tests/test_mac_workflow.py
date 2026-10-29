import base64
import hashlib
import io
import json
import re
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'mac'))
from PIL import Image
from maps import (PackError, Cancelled, decode_tile, download_tiles, generate_resources,
                  plan_route, tile_path, tile_image, APP_ID, PROGRAM_VERSION, preview_bounds)
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

    def test_preview_levels_are_local_and_cover_all_base_tiles(self):
        self.seed()
        with patch('urllib.request.urlopen',side_effect=AssertionError('preview must be offline')):
            generate_resources(self.plan,self.root/'cache',self.root/'watch',self.cancel,lambda *a:None)
        manifest=json.loads((self.root/'watch/map-manifest.json').read_text())
        base=self.plan['zooms'][0]
        self.assertLess(manifest['preview_min_zoom'],base)
        for z in range(manifest['preview_min_zoom'],base):
            parents={(t[1]//2**(base-z),t[2]//2**(base-z)) for t in self.plan['tiles']}
            paths=list((self.root/'watch/resources').glob('Preview%d_*.png'%z))
            self.assertEqual(len(paths),len(parents))
            for path in paths:
                self.assertEqual(decode_tile(path.read_bytes()).size,(256,256))

    def test_preview_leaves_undownloaded_quadrants_blank(self):
        self.seed()
        one={**self.plan,'tiles':self.plan['tiles'][:1]}
        generate_resources(one,self.root/'cache',self.root/'watch',self.cancel,lambda *a:None)
        tile=one['tiles'][0]; z=tile[0]-1
        image=Image.open(next((self.root/'watch/resources').glob('Preview%d_*.png'%z)))
        # A remote corner of the opposite quadrant must not inherit the child map.
        px=245 if tile[1]%2==0 else 10
        py=245 if tile[2]%2==0 else 10
        self.assertEqual(image.getpixel((px,py)),(220,226,214))

    def test_preview_overview_fits_route_in_small_watch(self):
        bounds,minimum=preview_bounds(self.plan)
        scale=2**(minimum-self.plan['zooms'][0])
        self.assertLessEqual(max(bounds[2]-bounds[0],bounds[3]-bounds[1])*scale,100)

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
        (folder/'BCTRAIL.prg').write_bytes(data)
        (folder/'program.json').write_text(json.dumps({'device':'fr255','app_id':APP_ID,'program_version':PROGRAM_VERSION,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}))
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
