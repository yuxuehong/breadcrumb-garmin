"""Read-only course import, using temporary volumes and a fake USB library only."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mac'))
import device
from maps import PackError


class VolumeCourseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.apps = self.root / 'GARMIN/APPS'
        self.apps.mkdir(parents=True)
        self.device_xml = self.apps.parent / 'GarminDevice.xml'
        self.device_xml.write_text('<Device><Id>synthetic-unit-a</Id></Device>')
        self.courses = self.apps.parent / 'Courses'
        self.courses.mkdir()
        self.gpx = self.courses / '千岛湖.gPx'
        self.gpx.write_bytes(b'<gpx>synthetic test route</gpx>')
        self.detected = {'ok': True, 'device': 'fr255', 'name': 'Forerunner 255',
                         'transport': 'volume', 'apps': str(self.apps)}
        self.detect_patch = patch('device.detect', return_value=self.detected)
        self.detect_patch.start()
        self.addCleanup(self.detect_patch.stop)

    def test_list_exposes_only_course_metadata_and_stable_opaque_ids(self):
        (self.courses / 'secret.txt').write_text('do not expose')
        (self.courses / 'subfolder.fit').mkdir()
        (self.courses / 'empty.fit').touch()
        (self.courses / 'oversized.fit').write_bytes(b'x')
        with (self.courses / 'oversized.fit').open('r+b') as file:
            file.truncate(device.MAX_COURSE_BYTES + 1)
        (self.courses / 'linked.fit').symlink_to(self.gpx)
        activity = self.apps.parent / 'Activities'
        activity.mkdir()
        (activity / 'private.fit').write_bytes(b'private')
        first = device.list_courses()
        self.assertEqual(first, device.list_courses())
        self.assertEqual(len(first['courses']), 1)
        course = first['courses'][0]
        self.assertEqual(set(course), {'id', 'name', 'bytes', 'format'})
        self.assertRegex(course['id'], r'^[0-9a-f]{64}$')
        self.assertEqual(course['name'], self.gpx.name)
        self.assertEqual(course['format'], 'gpx')
        self.assertNotIn(str(self.root), json.dumps(first))

    def test_read_returns_exact_bytes_and_preserves_all_device_files(self):
        (self.apps / 'OTHER.PRG').write_bytes(b'unrelated')
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        course = device.list_courses()['courses'][0]
        read = device.read_course(course['id'])
        self.assertEqual(read, {**course, 'data': self.gpx.read_bytes()})
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_arbitrary_paths_and_unknown_ids_are_rejected(self):
        for invalid in ('../../Activities/private.fit', str(self.gpx), '42', None, '0' * 64):
            with self.subTest(invalid=invalid), self.assertRaises(PackError):
                device.read_course(invalid)

    def test_changed_course_requires_new_selection(self):
        identifier = device.list_courses()['courses'][0]['id']
        self.gpx.write_bytes(b'updated synthetic route')
        with self.assertRaisesRegex(PackError, '已改变'):
            device.read_course(identifier)

    def test_same_model_watch_change_invalidates_list_and_pre_read_check(self):
        detected, entries = device._course_listing()
        identifier = entries[0]['id']
        self.device_xml.write_text('<Device><Id>synthetic-unit-b</Id></Device>')
        self.assertNotEqual(identifier, device.list_courses()['courses'][0]['id'])
        with self.assertRaisesRegex(PackError, '已改变'):
            device.read_course(identifier)
        with patch('device._course_listing', return_value=(detected, entries)):
            with self.assertRaisesRegex(PackError, '手表已改变'):
                device.read_course(identifier)

    def test_missing_stable_volume_identity_is_rejected(self):
        self.device_xml.write_text('<Device><Description>Forerunner 255</Description></Device>')
        with self.assertRaisesRegex(PackError, '身份'):
            device.list_courses()

    def test_replaced_symlink_is_never_read(self):
        identifier = device.list_courses()['courses'][0]['id']
        self.gpx.unlink()
        self.gpx.symlink_to(self.apps / 'OTHER.PRG')
        with self.assertRaises(PackError):
            device.read_course(identifier)

    def test_directory_changed_after_enumeration_cannot_redirect_read(self):
        detected, entries = device._course_listing()
        moved = self.root / 'moved-courses'
        self.courses.rename(moved)
        self.courses.symlink_to(moved, target_is_directory=True)
        with patch('device._course_listing', return_value=(detected, entries)):
            with self.assertRaisesRegex(PackError, '无法读取'):
                device.read_course(entries[0]['id'])

    def test_directory_symlinks_are_rejected_and_missing_directory_is_empty(self):
        self.gpx.unlink()
        self.courses.rmdir()
        self.assertEqual(device.list_courses()['courses'], [])
        self.courses.symlink_to(self.apps, target_is_directory=True)
        with self.assertRaisesRegex(PackError, '目录异常'):
            device.list_courses()


class MtpCourseApiTests(unittest.TestCase):
    def setUp(self):
        self.detected = {'ok': True, 'device': 'fr255', 'name': 'Forerunner 255', 'transport': 'mtp'}
        self.listing = {**self.detected, 'device_key': 'a' * 64, 'courses': [
            {'storage_id': 1, 'object_id': 42, 'name': 'Test.FIT', 'bytes': 4}]}
        detector = patch('device.detect', return_value=self.detected)
        detector.start()
        self.addCleanup(detector.stop)

    def test_mtp_revalidates_id_and_cleans_temporary_copy(self):
        calls = []
        def bridge(*args, **kwargs):
            calls.append(args)
            if args[0] == 'list-courses':
                return self.listing
            self.assertEqual(args[:5], ('read-course', 1, 42, 4, 'Test.FIT'))
            Path(args[5]).write_bytes(b'FIT!')
            return {'ok': True, 'bytes': 4}
        with patch('device.mtp', side_effect=bridge):
            public = device.list_courses()['courses'][0]
            result = device.read_course(public['id'])
        self.assertEqual(result['data'], b'FIT!')
        self.assertEqual([c[0] for c in calls], ['list-courses', 'list-courses', 'read-course'])
        self.assertFalse(Path(calls[-1][5]).exists())
        self.assertNotIn('storage_id', public)
        self.assertNotIn('object_id', public)
        self.assertNotIn('device_key', public)
        self.assertEqual(calls[-1][-1], 'a' * 64)

    def test_stale_id_never_reaches_mtp_read(self):
        with patch('device.mtp', return_value=self.listing) as bridge:
            identifier = device.list_courses()['courses'][0]['id']
            self.listing['courses'][0]['object_id'] = 43
            with self.assertRaisesRegex(PackError, '已改变'):
                device.read_course(identifier)
            self.assertTrue(all(call.args[0] == 'list-courses' for call in bridge.call_args_list))

    def test_same_model_mtp_watch_change_invalidates_ids(self):
        with patch('device.mtp', return_value=self.listing) as bridge:
            identifier = device.list_courses()['courses'][0]['id']
            self.listing['device_key'] = 'b' * 64
            with self.assertRaisesRegex(PackError, '已改变'):
                device.read_course(identifier)
            self.assertTrue(all(call.args[0] == 'list-courses' for call in bridge.call_args_list))

    def test_missing_mtp_identity_does_not_fall_back_to_model(self):
        self.listing.pop('device_key')
        with patch('device.mtp', return_value=self.listing), self.assertRaisesRegex(PackError, '身份'):
            device.list_courses()

    def test_invalid_mtp_metadata_and_short_copy_are_rejected(self):
        for key, value in (('name', '../private.fit'), ('bytes', device.MAX_COURSE_BYTES + 1),
                           ('object_id', 0), ('storage_id', '1')):
            with self.subTest(key=key), patch('device.mtp', return_value={
                    **self.listing, 'courses': [{**self.listing['courses'][0], key: value}]}):
                with self.assertRaises(PackError):
                    device.list_courses()
        def short_copy(*args, **kwargs):
            if args[0] == 'list-courses':
                return self.listing
            Path(args[5]).write_bytes(b'bad')
            return {'ok': True}
        with patch('device.mtp', side_effect=short_copy):
            identifier = device.list_courses()['courses'][0]['id']
            with self.assertRaisesRegex(PackError, '复制不完整'):
                device.read_course(identifier)


@unittest.skipUnless(Path('/opt/homebrew/include/libmtp.h').exists() and shutil.which('cc'),
                     'local libmtp compiler dependencies unavailable')
class MtpCourseBridgeTests(unittest.TestCase):
    """Compile actual C bridge against fake functions; never links USB library."""
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.bridge = cls.root / 'course-bridge'
        stub = cls.root / 'mtp-stub.c'
        stub.write_text(r'''
#include <libmtp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static LIBMTP_mtpdevice_t mock_device;
static LIBMTP_devicestorage_t mock_storage;
void LIBMTP_Init(void) {}
LIBMTP_error_number_t LIBMTP_Detect_Raw_Devices(LIBMTP_raw_device_t **raw,int *count) {
    *raw=calloc(1,sizeof **raw);(*raw)->device_entry.vendor_id=0x091e;*count=1;
    return LIBMTP_ERROR_NONE;
}
LIBMTP_mtpdevice_t *LIBMTP_Open_Raw_Device_Uncached(LIBMTP_raw_device_t *raw) {
    mock_storage.id=1;mock_storage.FreeSpaceInBytes=10000000;
    mock_device.storage=&mock_storage;return &mock_device;
}
void LIBMTP_Release_Device(LIBMTP_mtpdevice_t *device) {}
char *LIBMTP_Get_Modelname(LIBMTP_mtpdevice_t *device) { return strdup("Forerunner 255"); }
char *LIBMTP_Get_Serialnumber(LIBMTP_mtpdevice_t *device) { return strdup(getenv("COURSE_DEVICE")); }
int LIBMTP_Get_Storage(LIBMTP_mtpdevice_t *device,int sort) { return 0; }
LIBMTP_file_t *LIBMTP_new_file_t(void) { return calloc(1,sizeof(LIBMTP_file_t)); }
void LIBMTP_destroy_file_t(LIBMTP_file_t *file) { free(file->filename);free(file); }
static LIBMTP_file_t *entry(char *name,uint32_t id,uint64_t size,int folder) {
    LIBMTP_file_t *file=LIBMTP_new_file_t();file->filename=strdup(name);
    file->item_id=id;file->filesize=size;
    file->filetype=folder?LIBMTP_FILETYPE_FOLDER:LIBMTP_FILETYPE_UNKNOWN;return file;
}
LIBMTP_file_t *LIBMTP_Get_Files_And_Folders(LIBMTP_mtpdevice_t *device,uint32_t storage,uint32_t parent) {
    fprintf(stderr,"LIST:%u\n",parent);
    if(parent==LIBMTP_FILES_AND_FOLDERS_ROOT)return entry("GARMIN",1,0,1);
    if(parent==1) {
        LIBMTP_file_t *file=entry("COURSES",3,0,1);
        file->next=entry("ACTIVITIES",4,0,1);return file;
    }
    if(parent!=3)abort();
    LIBMTP_file_t *file=entry("Test.FIT",42,4,0),*tail=file;
    tail=tail->next=entry("route.gpx",43,6,0);
    tail=tail->next=entry("private.txt",44,4,0);
    tail=tail->next=entry("huge.fit",45,20971521,0);
    tail=tail->next=entry("folder.fit",46,4,1);
    tail=tail->next=entry("../outside.fit",47,4,0);
    return file;
}
int LIBMTP_Get_File_To_File(LIBMTP_mtpdevice_t *device,uint32_t id,const char *path,
                          LIBMTP_progressfunc_t callback,const void *data) {
    fprintf(stderr,"READ:%u\n",id);if(id!=42 && id!=43)abort();
    const char *fault=getenv("COURSE_FAULT");
    if(fault && !strcmp(fault,"progress") && callback && callback(20971521,20971521,data))return -1;
    FILE *file=fopen(path,"wb");if(!file)return -1;
    fputs(fault && !strcmp(fault,"short")?"BAD":"FIT!",file);fclose(file);return 0;
}
int LIBMTP_Send_File_From_File(LIBMTP_mtpdevice_t *device,const char *path,LIBMTP_file_t *file,
                             LIBMTP_progressfunc_t callback,const void *data) {abort();}
int LIBMTP_Delete_Object(LIBMTP_mtpdevice_t *device,uint32_t id) {abort();}
''')
        subprocess.run(['cc', '-O2', str(ROOT / 'mac/mtp_bridge.c'), str(stub),
                        '-I/opt/homebrew/include', '-o', str(cls.bridge)], check=True, capture_output=True)

    def run_bridge(self, *args, fault='', hardware='synthetic-device-a', device_key=None):
        if args[0] == 'read-course':
            args = (*args, device_key or hashlib.sha256(b'synthetic-device-a').hexdigest())
        process = subprocess.run([str(self.bridge), *map(str, args)], capture_output=True, text=True,
                                 env={**os.environ, 'COURSE_FAULT': fault, 'COURSE_DEVICE': hardware}, timeout=5)
        return process.returncode, json.loads(process.stdout), process.stderr

    def test_lists_only_valid_courses_and_never_opens_other_directories(self):
        code, listing, trace = self.run_bridge('list-courses')
        self.assertEqual(code, 0)
        self.assertEqual([item['object_id'] for item in listing['courses']], [42, 43])
        self.assertNotIn('LIST:4\n', trace)
        self.assertNotIn('READ:', trace)
        self.assertNotIn('synthetic-device-a', json.dumps(listing))
        self.assertNotIn('synthetic-device-a', trace)

    def test_valid_course_download_matches_and_makes_no_device_mutations(self):
        output = self.root / 'valid.fit'
        code, report, trace = self.run_bridge('read-course', 1, 42, 4, 'Test.FIT', output, 'fr255')
        self.assertEqual(code, 0)
        self.assertTrue(report['ok'])
        self.assertEqual(output.read_bytes(), b'FIT!')
        self.assertIn('READ:42', trace)

    def test_unlisted_objects_paths_stale_metadata_and_other_models_are_rejected(self):
        for index, selection in enumerate(((1, 99, 4, 'Test.FIT', 'fr255'),
                (2, 42, 4, 'Test.FIT', 'fr255'), (1, 42, 5, 'Test.FIT', 'fr255'),
                (1, 42, 4, '../Test.FIT', 'fr255'), (1, 42, 4, 'Test.FIT', 'fr255s'),
                (1, 45, 20971521, 'huge.fit', 'fr255'))):
            with self.subTest(selection=selection):
                sid, oid, size, name, model = selection
                output = self.root / f'rejected-{index}'
                code, report, trace = self.run_bridge('read-course', sid, oid, size, name, output, model)
                self.assertEqual(code, 1)
                self.assertFalse(report['ok'])
                self.assertNotIn('READ:', trace)
                self.assertFalse(output.exists())

    def test_short_and_overlimit_transfers_fail_and_remove_only_local_partial_copy(self):
        for fault in ('short', 'progress'):
            output = self.root / f'partial-{fault}'
            code, report, trace = self.run_bridge('read-course', 1, 42, 4, 'Test.FIT', output, 'fr255', fault=fault)
            self.assertEqual(code, 1)
            self.assertFalse(report['ok'])
            self.assertFalse(output.exists())

    def test_existing_local_destination_is_preserved(self):
        output = self.root / 'existing.fit'
        output.write_bytes(b'keep')
        code, report, trace = self.run_bridge('read-course', 1, 42, 4, 'Test.FIT', output, 'fr255')
        self.assertEqual(code, 1)
        self.assertNotIn('READ:', trace)
        self.assertEqual(output.read_bytes(), b'keep')

    def test_same_model_device_change_is_rejected_before_course_read(self):
        output = self.root / 'swapped.fit'
        code, report, trace = self.run_bridge('read-course', 1, 42, 4, 'Test.FIT', output, 'fr255',
                                               hardware='synthetic-device-b')
        self.assertEqual(code, 1)
        self.assertIn('手表已改变', report['error'])
        self.assertNotIn('READ:', trace)
        self.assertFalse(output.exists())

    def test_missing_hardware_identity_is_not_accepted(self):
        code, report, trace = self.run_bridge('list-courses', hardware='')
        self.assertEqual(code, 1)
        self.assertIn('身份', report['error'])


if __name__ == '__main__':
    unittest.main()
