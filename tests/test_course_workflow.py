"""Desktop course workflows with fake USB metadata and synthetic routes only."""
import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'mac'))
import server
from maps import PackError
from test_courses import fit_course

REAL_THREAD = threading.Thread
DEFAULT_PAYLOAD = object()
GPX = b'<gpx><trk><name>Saved race</name><trkseg><trkpt lat="30" lon="119"/><trkpt lat="30.001" lon="119.002"/></trkseg></trk></gpx>'


class CourseWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.desktop = server.Desktop(self.root/'desktop')
        self.old = self.desktop.import_route({'gpx':base64.b64encode(GPX).decode(),'zoom':14,'buffer':150})['id']
        self.old_plan = (self.desktop.projects/self.old/'plan.json').read_bytes()
        self.data = fit_course(name='Synthetic course')
        self.item = {'id':'a'*64,'name':'synthetic.fit','bytes':len(self.data),'format':'fit'}
        self.listing = {'ok':True,'device':'fr255','name':'Forerunner 255','transport':'mtp',
                        'courses':[self.item]}
        self.read = {**self.item,'data':self.data}
        self.download = self.start_patch('server.download_tiles')
        self.build = self.start_patch('server.compile_program')
        self.install = self.start_patch('server.install')
        self.list_usb = self.start_patch('server.watch_device.list_courses',return_value=self.listing)
        self.read_usb = self.start_patch('server.watch_device.read_course',return_value=self.read)
        self.start_patch('server.find_sdk',side_effect=PackError('test has no SDK'))
        self.worker = self.start_patch('server.threading.Thread')

    def start_patch(self, target, **kwargs):
        patcher = patch(target,**kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def run_worker(self):
        kwargs = self.worker.call_args.kwargs
        kwargs['target'](*kwargs.get('args',()))

    def scan(self):
        self.desktop.scan_courses()
        self.run_worker()

    def old_intact(self):
        self.assertEqual(self.desktop.current,self.old)
        self.assertEqual((self.desktop.projects/self.old/'route.gpx').read_bytes(),GPX)
        self.assertEqual((self.desktop.projects/self.old/'plan.json').read_bytes(),self.old_plan)
        self.assertFalse(list(self.desktop.data.glob('.route-*')))

    def no_downloads_or_install(self):
        self.download.assert_not_called()
        self.build.assert_not_called()
        self.install.assert_not_called()
        self.assertFalse(self.desktop.cache.exists())

    def test_scan_is_background_read_only_and_keeps_current_project(self):
        self.desktop.scan_courses()
        state = self.desktop.summary()
        self.assertTrue(state['job']['active'])
        self.assertEqual(state['job']['kind'],'courses')
        self.assertEqual(state['courses']['status'],'loading')
        self.list_usb.assert_not_called()
        self.run_worker()
        state = self.desktop.summary()
        self.assertEqual(state['courses'],{'status':'ready','device':'Forerunner 255',
                                           'items':[self.item],'error':''})
        self.assertFalse(state['job']['active'])
        self.read_usb.assert_not_called()
        self.old_intact()
        self.no_downloads_or_install()

    def test_selected_fit_import_creates_plan_without_downloading(self):
        self.scan()
        self.desktop.import_course({'id':self.item['id'],'zoom':14,'buffer':150})
        self.assertEqual(self.desktop.summary()['job']['kind'],'import-course')
        self.read_usb.assert_not_called()
        self.run_worker()
        self.read_usb.assert_called_once_with(self.item['id'])
        result = self.desktop.summary()
        self.assertNotEqual(result['id'],self.old)
        self.assertEqual(result['plan']['route']['name'],'Synthetic course')
        self.assertEqual(result['plan']['zooms'],[14])
        self.assertEqual(result['job']['stage'],'planned')
        self.assertEqual(result['courses']['status'],'idle')
        self.assertEqual(result['courses']['items'],[])
        self.assertEqual(self.desktop.course_ids,{})
        self.assertEqual((self.desktop.projects/self.old/'route.gpx').read_bytes(),GPX)
        self.assertFalse(list(self.desktop.data.glob('.route-*')))
        self.no_downloads_or_install()

    def test_course_scan_syncs_exact_model_without_private_device_metadata(self):
        for model in ('fr255s','fr255m','fr255sm'):
            with self.subTest(model=model):
                self.desktop.device = {'device':'fr255','name':'Forerunner 255','serial':'old-private'}
                self.list_usb.return_value = {**self.listing,'device':model,'name':server.DEVICES[model],
                                              'serial':'private-serial','device_key':'private-key'}
                self.scan()
                result = self.desktop.summary()
                self.assertEqual(result['device'],{'device':model,'name':server.DEVICES[model],
                                                   'transport':'mtp'})
                self.assertEqual(result['courses']['device'],server.DEVICES[model])

    def test_selected_gpx_import_preserves_original_bytes(self):
        self.item.update(name='synthetic.gpx',format='gpx',bytes=len(GPX))
        self.read_usb.return_value = {**self.item,'data':GPX}
        self.scan()
        self.desktop.import_course({'id':self.item['id'],'zoom':14,'buffer':150})
        self.run_worker()
        self.assertEqual((self.desktop.projects/self.desktop.current/'route.gpx').read_bytes(),GPX)
        self.no_downloads_or_install()

    def test_unissued_and_stale_ids_never_reach_usb(self):
        with self.assertRaises(PackError):
            self.desktop.import_course({'id':self.item['id']})
        self.scan()
        for identity in ('../private','b'*64,None):
            with self.subTest(identity=identity), self.assertRaises(PackError):
                self.desktop.import_course({'id':identity})
        self.list_usb.return_value = {**self.listing,'courses':[]}
        self.scan()
        with self.assertRaises(PackError):
            self.desktop.import_course({'id':self.item['id']})
        self.read_usb.assert_not_called()

    def test_scan_failure_revokes_previous_list_and_preserves_map(self):
        self.scan()
        self.list_usb.side_effect = PackError('USB disconnected')
        self.scan()
        state = self.desktop.summary()
        self.assertEqual(state['courses']['status'],'error')
        self.assertEqual(state['courses']['error'],'USB disconnected')
        self.assertEqual(state['courses']['items'],[])
        self.assertFalse(state['job']['active'])
        self.old_intact()

    def test_import_read_parse_and_plan_failures_keep_old_map_and_list(self):
        self.scan()
        for failing_part in ('read','parse','plan'):
            with self.subTest(part=failing_part):
                target = {'read':'server.watch_device.read_course','parse':'server.parse_course',
                          'plan':'server.plan_route'}[failing_part]
                with patch(target,side_effect=PackError('synthetic failure')):
                    self.desktop.import_course({'id':self.item['id'],'zoom':14,'buffer':150})
                    self.run_worker()
                self.assertEqual(self.desktop.job['stage'],'error')
                self.assertEqual(self.desktop.courses['status'],'ready')
                self.old_intact()
        self.no_downloads_or_install()

    def test_changed_course_metadata_is_rejected_before_parsing(self):
        self.scan()
        self.read_usb.return_value = {**self.read,'name':'changed.fit'}
        with patch('server.parse_course') as parse:
            self.desktop.import_course({'id':self.item['id']})
            self.run_worker()
            parse.assert_not_called()
        self.assertEqual(self.desktop.job['stage'],'error')
        self.old_intact()

    def test_busy_course_job_blocks_all_conflicting_project_actions(self):
        self.scan()
        for kind in ('scan','import'):
            if kind=='scan': self.desktop.scan_courses()
            else:
                self.run_worker()
                self.desktop.import_course({'id':self.item['id']})
            operations = [lambda:self.desktop.scan_courses(),
                          lambda:self.desktop.import_course({'id':self.item['id']}),
                          lambda:self.desktop.import_route({'gpx':base64.b64encode(GPX).decode()}),
                          lambda:self.desktop.select(self.old),
                          lambda:self.desktop.update_options({'zoom':15}),
                          lambda:self.desktop.start('download')]
            for operation in operations:
                with self.subTest(kind=kind,operation=operation), self.assertRaises(PackError):
                    operation()
        self.old_intact()
        self.no_downloads_or_install()

    def test_cancel_before_worker_prevents_usb_access(self):
        self.desktop.scan_courses()
        self.desktop.cancel_job()
        self.run_worker()
        self.list_usb.assert_not_called()
        self.assertEqual(self.desktop.courses['status'],'idle')
        self.assertEqual(self.desktop.job['stage'],'paused')
        self.scan()
        self.desktop.import_course({'id':self.item['id']})
        self.desktop.cancel_job()
        self.run_worker()
        self.read_usb.assert_not_called()
        self.assertEqual(self.desktop.courses['status'],'ready')
        self.old_intact()

    def test_cancellation_after_usb_read_discards_result_without_parsing(self):
        self.scan()
        def read(_):
            self.desktop.cancel_job()
            self.assertTrue(self.desktop.job['active'])
            return self.read
        self.read_usb.side_effect = read
        with patch('server.parse_course') as parse:
            self.desktop.import_course({'id':self.item['id']})
            self.run_worker()
            parse.assert_not_called()
        self.assertEqual(self.desktop.job['stage'],'paused')
        self.assertEqual(self.desktop.courses['status'],'ready')
        self.old_intact()

    def test_cancellation_during_plan_never_publishes_partial_project(self):
        self.scan()
        original = server.plan_route
        def plan(*args):
            value = original(*args)
            self.desktop.cancel_job()
            return value
        with patch('server.plan_route',side_effect=plan):
            self.desktop.import_course({'id':self.item['id'],'zoom':14,'buffer':150})
            self.run_worker()
        self.assertEqual(self.desktop.job['stage'],'paused')
        self.assertEqual(len(list(self.desktop.projects.iterdir())),1)
        self.old_intact()

    def test_usb_calls_do_not_hold_desktop_lock_and_cancel_waits_for_read(self):
        # A real worker is used only with a fake blocking USB call.
        self.scan()
        entered, release = threading.Event(), threading.Event()
        def read(_):
            entered.set()
            if not release.wait(2): raise RuntimeError('test USB release timeout')
            return self.read
        self.read_usb.side_effect = read
        self.desktop.import_course({'id':self.item['id']})
        worker = REAL_THREAD(target=self.run_worker)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertTrue(self.desktop.lock.acquire(timeout=.2))
            self.desktop.lock.release()
            self.desktop.cancel_job()
            self.assertTrue(self.desktop.job['active'])
            self.assertEqual(self.desktop.current,self.old)
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.desktop.job['stage'],'paused')
        self.old_intact()

    def request(self,path,payload=DEFAULT_PAYLOAD,token='test-token'):
        response = []
        handler = object.__new__(server.Handler)
        data = json.dumps({} if payload is DEFAULT_PAYLOAD else payload).encode()
        handler.server = SimpleNamespace(server_port=12345,token='test-token',desktop=self.desktop)
        handler.headers = {'Host':'127.0.0.1:12345','Content-Length':str(len(data)),
                           'X-Trail-Token':token,'Origin':'http://127.0.0.1:12345'}
        handler.path, handler.rfile = path, io.BytesIO(data)
        handler.respond = lambda status, value:response.append((status,value))
        handler.do_POST()
        return response[0]

    def test_http_routes_use_existing_authorization_and_background_contract(self):
        self.assertEqual(self.request('/api/courses',token='wrong')[0],403)
        self.list_usb.assert_not_called()
        status, response = self.request('/api/courses')
        self.assertEqual(status,200)
        self.assertEqual(response['courses']['status'],'loading')
        self.run_worker()
        status, response = self.request('/api/import-course',{'id':self.item['id'],'zoom':14,'buffer':150})
        self.assertEqual(status,200)
        self.assertEqual(response['job']['kind'],'import-course')
        self.assertEqual(self.request('/api/device')[0],400)
        self.assertEqual(self.request('/api/cancel')[0],200)
        self.run_worker()
        self.assertEqual(self.desktop.job['stage'],'paused')
        self.old_intact()

    def test_http_non_object_payload_is_a_friendly_400_without_starting_work(self):
        for payload in (None, [], ['not-an-object'], 'text', 3, False):
            with self.subTest(payload=payload):
                status, result = self.request('/api/import-course',payload)
                self.assertEqual(status,400)
                self.assertEqual(result['error'],'请求格式不正确，请重新选择路线后重试。')
        self.worker.assert_not_called()
        self.list_usb.assert_not_called()
        self.read_usb.assert_not_called()
        self.old_intact()

    def test_summary_copies_do_not_change_issued_metadata(self):
        self.scan()
        state = self.desktop.summary()
        state['courses']['items'][0]['name'] = 'changed.fit'
        self.assertEqual(self.desktop.courses['items'][0]['name'],'synthetic.fit')
        self.assertEqual(self.desktop.course_ids[self.item['id']]['name'],'synthetic.fit')


if __name__=='__main__':
    unittest.main()
