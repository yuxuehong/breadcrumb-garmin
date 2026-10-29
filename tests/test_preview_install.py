"""USB bundle failure paths; these tests never open a physical USB device."""
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
sys.path.insert(0,str(ROOT/'mac'))
import device
from maps import APP_ID, PREVIEW_APP_ID, PROGRAM_VERSION, PackError


class PreviewInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.folder=self.root/'bundle';self.folder.mkdir()
        self.apps=self.root/'GARMIN/APPS';self.apps.mkdir(parents=True)
        self.backups=self.root/'backups'
        main=b'data-field-map'*100
        preview=b'standalone-preview'*100
        (self.folder/'BCTRAIL.prg').write_bytes(main)
        (self.folder/'BCPREVW.prg').write_bytes(preview)
        self.metadata={'device':'fr255','app_id':APP_ID,'program_version':PROGRAM_VERSION,
                       'bytes':len(main),'sha256':hashlib.sha256(main).hexdigest(),
                       'preview':{'filename':'BCPREVW.prg','app_id':PREVIEW_APP_ID,
                                  'bytes':len(preview),'sha256':hashlib.sha256(preview).hexdigest()},
                       'total_bytes':len(main)+len(preview)}
        self.save_metadata()
        (self.apps/'BCTRAIL.PRG').write_bytes(b'previous-map')
        (self.apps/'BCPREVW.PRG').write_bytes(b'previous-preview')
        (self.apps/'OTHER.PRG').write_bytes(b'unrelated')
        self.detected={'device':'fr255','transport':'volume','apps':str(self.apps)}

    def save_metadata(self):
        (self.folder/'program.json').write_text(json.dumps(self.metadata))

    def install(self):
        with patch('device.detect',return_value=self.detected):
            return device.install(self.folder,self.backups)

    def test_both_programs_are_verified_and_backed_up(self):
        result=self.install()
        self.assertEqual(result['status'],'copied_verified')
        self.assertEqual(list(result['programs']),['BCPREVW.PRG','BCTRAIL.PRG'])
        backup=Path(result['backup'])
        self.assertEqual((backup/'previous.prg').read_bytes(),b'previous-map')
        self.assertEqual((backup/'preview-previous.prg').read_bytes(),b'previous-preview')
        for filename,prefix in [('BCTRAIL',''),('BCPREVW','preview-')]:
            expected=(self.folder/(filename+'.prg')).read_bytes()
            self.assertEqual((self.apps/(filename+'.PRG')).read_bytes(),expected)
            self.assertEqual((backup/(prefix+'readback.prg')).read_bytes(),expected)
        self.assertEqual((self.apps/'OTHER.PRG').read_bytes(),b'unrelated')
        self.assertFalse(result['hardware_verified'])
        self.assertEqual(json.loads((backup/'installation.json').read_text())['status'],'copied_verified')

    def test_missing_preview_stops_before_any_write(self):
        (self.folder/'BCPREVW.prg').unlink()
        with self.assertRaises(PackError):self.install()
        self.assertFalse(self.backups.exists())
        self.assertEqual((self.apps/'BCTRAIL.PRG').read_bytes(),b'previous-map')
        self.assertEqual((self.apps/'BCPREVW.PRG').read_bytes(),b'previous-preview')

    def test_tampered_preview_stops_before_any_write(self):
        (self.folder/'BCPREVW.prg').write_bytes(b'x'*self.metadata['preview']['bytes'])
        with patch('device.install_volume_file') as copy:
            with self.assertRaises(PackError):self.install()
        copy.assert_not_called()
        self.assertFalse(self.backups.exists())

    def test_tampered_main_stops_before_preview_copy(self):
        (self.folder/'BCTRAIL.prg').write_bytes(b'x'*self.metadata['bytes'])
        with patch('device.install_volume_file') as copy:
            with self.assertRaises(PackError):self.install()
        copy.assert_not_called()

    def test_preview_identity_and_filename_are_fixed(self):
        for key,value in [('app_id',APP_ID),('filename','../../OTHER.prg')]:
            previous=self.metadata['preview'][key]
            self.metadata['preview'][key]=value;self.save_metadata()
            with self.assertRaises(PackError):self.install()
            self.metadata['preview'][key]=previous
        self.assertFalse(self.backups.exists())

    def test_combined_size_is_checked_before_first_copy(self):
        self.detected['free_bytes']=self.metadata['bytes']+1024*1024
        with patch('device.install_volume_file') as copy:
            with self.assertRaises(PackError):self.install()
        copy.assert_not_called()

    def test_stale_main_temporary_stops_before_preview_copy(self):
        (self.apps/'BCTRAIL.TMP').write_bytes(b'preserve')
        with patch('device.install_volume_file') as copy:
            with self.assertRaises(PackError):self.install()
        copy.assert_not_called()
        self.assertEqual((self.apps/'BCTRAIL.TMP').read_bytes(),b'preserve')

    def test_second_program_failure_is_explicit_partial_install(self):
        real_copy=device.install_volume_file
        def fail_main(apps,filename,*args):
            if filename=='BCTRAIL.PRG':raise OSError('connection lost')
            return real_copy(apps,filename,*args)
        with patch('device.install_volume_file',side_effect=fail_main):
            with self.assertRaisesRegex(PackError,'赛道预览已写入，但运动地图安装未完成'):
                self.install()
        journal=json.loads(next(self.backups.glob('*/installation.json')).read_text())
        self.assertEqual(journal['status'],'partial')
        self.assertEqual(journal['completed'],['BCPREVW.PRG'])
        self.assertEqual(journal['failed'],'BCTRAIL.PRG')
        self.assertEqual((self.apps/'BCTRAIL.PRG').read_bytes(),b'previous-map')

    def test_failed_main_readback_restores_main_and_keeps_both_backups(self):
        real_copy=shutil.copyfile
        def corrupt_readback(source,destination,*args,**kwargs):
            result=real_copy(source,destination,*args,**kwargs)
            if Path(destination).name=='readback.prg':Path(destination).write_bytes(b'corrupt')
            return result
        with patch('device.shutil.copyfile',side_effect=corrupt_readback):
            with self.assertRaisesRegex(PackError,'运动地图安装未完成'):self.install()
        backup=next(self.backups.iterdir())
        self.assertEqual((self.apps/'BCTRAIL.PRG').read_bytes(),b'previous-map')
        self.assertEqual((backup/'previous.prg').read_bytes(),b'previous-map')
        self.assertEqual((backup/'preview-previous.prg').read_bytes(),b'previous-preview')
        self.assertFalse(list(self.apps.glob('*.TMP')))

    def test_mtp_receives_only_fixed_targets_in_preview_first_order(self):
        self.detected={'device':'fr255','transport':'mtp','free_bytes':10**9}
        ok={'ok':True,'status':'copied_verified','replaced':False,'hardware_verified':False}
        with patch('device.mtp',return_value=ok) as bridge:
            result=self.install()
        self.assertEqual([call.args[-1] for call in bridge.call_args_list],['BCPREVW.PRG','BCTRAIL.PRG'])
        self.assertEqual(result['status'],'copied_verified')

    def test_mtp_failure_never_reports_bundle_success(self):
        self.detected={'device':'fr255','transport':'mtp','free_bytes':10**9}
        ok={'ok':True,'status':'copied_verified','replaced':False,'hardware_verified':False}
        with patch('device.mtp',side_effect=[ok,PackError('USB disconnected')]):
            with self.assertRaisesRegex(PackError,'运动地图安装未完成'):self.install()
        journal=json.loads(next(self.backups.glob('*/installation.json')).read_text())
        self.assertEqual(journal['status'],'partial')
        self.assertFalse(journal['ok'])


@unittest.skipUnless(Path('/opt/homebrew/include/libmtp.h').exists() and shutil.which('cc'),
                     'local libmtp compiler dependencies unavailable')
class MtpTargetAllowlistTests(unittest.TestCase):
    def test_invalid_target_is_rejected_before_usb_initialization(self):
        with tempfile.TemporaryDirectory() as temp:
            executable=Path(temp)/'mtp-bridge'
            subprocess.run(['cc','-O2','-Wall','-Wextra',str(ROOT/'mac/mtp_bridge.c'),
                            '-I/opt/homebrew/include','-L/opt/homebrew/lib','-lmtp','-o',str(executable)],
                           check=True,capture_output=True)
            for destination in ('OTHER.PRG','../BCTRAIL.PRG','BCPREVW.TMP'):
                result=subprocess.run([str(executable),'install','unused','fr255','unused-backup',
                                       'unused-readback',destination],capture_output=True,text=True,timeout=5)
                self.assertEqual(result.returncode,1)
                self.assertIn('不支持的手表程序文件名',json.loads(result.stdout)['error'])


@unittest.skipUnless(Path('/opt/homebrew/include/libmtp.h').exists() and shutil.which('cc'),
                     'local libmtp compiler dependencies unavailable')
class MtpRecoveryTests(unittest.TestCase):
    """Link the real bridge to fake libmtp functions, with no USB library linked."""
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root=Path(cls.temp.name)
        cls.bridge=cls.root/'fault-bridge'
        stub=cls.root/'mtp-stub.c'
        stub.write_text(r'''
#include <libmtp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static LIBMTP_mtpdevice_t mock_device;
static LIBMTP_devicestorage_t mock_storage;
static int uploads;
static int fault(const char *name) { return !strcmp(getenv("MTP_FAULT"),name); }
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
char *LIBMTP_Get_Serialnumber(LIBMTP_mtpdevice_t *device) { return strdup("synthetic-install-test"); }
int LIBMTP_Get_Storage(LIBMTP_mtpdevice_t *device,int sort) { return 0; }
LIBMTP_file_t *LIBMTP_new_file_t(void) { return calloc(1,sizeof(LIBMTP_file_t)); }
void LIBMTP_destroy_file_t(LIBMTP_file_t *file) { free(file->filename);free(file); }
LIBMTP_file_t *LIBMTP_Get_Files_And_Folders(LIBMTP_mtpdevice_t *device,uint32_t storage,uint32_t parent) {
    LIBMTP_file_t *file=LIBMTP_new_file_t();
    if(parent==LIBMTP_FILES_AND_FOLDERS_ROOT) {file->filename=strdup("GARMIN");file->item_id=1;}
    else if(parent==1) {file->filename=strdup("APPS");file->item_id=2;}
    else {file->filename=strdup("BCPREVW.PRG");file->item_id=10;file->filesize=3;}
    return file;
}
int LIBMTP_Get_File_To_File(LIBMTP_mtpdevice_t *device,uint32_t id,const char *path,
                          LIBMTP_progressfunc_t callback,const void *data) {
    fprintf(stderr,"READ:%u\n",id);
    FILE *file=fopen(path,"wb");if(!file)return -1;
    const char *content=id==10 || (id==30 && !fault("restore_corrupt")) ? "OLD" : "BAD";
    fwrite(content,1,3,file);fclose(file);return 0;
}
int LIBMTP_Send_File_From_File(LIBMTP_mtpdevice_t *device,const char *path,LIBMTP_file_t *file,
                             LIBMTP_progressfunc_t callback,const void *data) {
    uploads++;fprintf(stderr,"SEND:%d\n",uploads);
    file->item_id=uploads==1?20:30;
    if(uploads==1 && fault("unknown_id")) {file->item_id=0;return -1;}
    if(uploads==1 && fault("partial_id"))return -1;
    return 0;
}
int LIBMTP_Delete_Object(LIBMTP_mtpdevice_t *device,uint32_t id) {
    fprintf(stderr,"DELETE:%u\n",id);
    return id==20 && fault("cleanup_fail") ? -1 : 0;
}
''')
        subprocess.run(['cc','-O2',str(ROOT/'mac/mtp_bridge.c'),str(stub),
                        '-I/opt/homebrew/include','-o',str(cls.bridge)],check=True,capture_output=True)

    def run_fault(self, fault):
        folder=self.root/fault;folder.mkdir()
        program=folder/'new.prg';program.write_bytes(b'NEW'*100)
        result=subprocess.run([str(self.bridge),'install',str(program),'fr255',str(folder/'backup.prg'),
                               str(folder/'readback.prg'),'BCPREVW.PRG'],
                              env={**os.environ,'MTP_FAULT':fault},capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,1)
        self.assertEqual((folder/'backup.prg').read_bytes(),b'OLD')
        return json.loads(result.stdout),result.stderr

    def test_failed_cleanup_does_not_upload_duplicate_or_claim_restored(self):
        report,trace=self.run_fault('cleanup_fail')
        self.assertIn('自动恢复未完成',report['error'])
        self.assertNotIn('旧程序已恢复',report['error'])
        self.assertIn('DELETE:20',trace)
        self.assertNotIn('SEND:2',trace)

    def test_failed_upload_retains_partial_object_id_for_cleanup(self):
        report,trace=self.run_fault('partial_id')
        self.assertIn('旧程序已恢复',report['error'])
        self.assertLess(trace.index('DELETE:20'),trace.index('SEND:2'))
        self.assertIn('READ:30',trace)

    def test_unknown_partial_object_never_claims_restoration(self):
        report,trace=self.run_fault('unknown_id')
        self.assertIn('无法确认未完成传输的文件状态',report['error'])
        self.assertNotIn('SEND:2',trace)
        self.assertNotIn('旧程序已恢复',report['error'])

    def test_corrupt_restore_readback_is_reported_as_incomplete(self):
        report,trace=self.run_fault('restore_corrupt')
        self.assertIn('自动恢复未完成',report['error'])
        self.assertIn('SEND:2',trace)
        self.assertIn('READ:30',trace)
        self.assertNotIn('旧程序已恢复',report['error'])


if __name__=='__main__':unittest.main()
