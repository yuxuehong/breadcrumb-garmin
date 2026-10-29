/* Minimal libmtp bridge. Only GARMIN/APPS/BCTRAIL.PRG and BCPREVW.PRG are written.
 * Course import reads only direct FIT/GPX files in GARMIN/COURSES, up to 20 MiB.
 * No activities, health records, music or unrelated applications are read.
 * The selected program is backed up before replacement and restored on failure.
 */
#include <libmtp.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <errno.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>
#include <sys/stat.h>
#include <CommonCrypto/CommonDigest.h>

static FILE *result;
static LIBMTP_mtpdevice_t *device;
static uint32_t storage, apps;
static const char *destination="BCTRAIL.PRG";
#define MAX_COURSE_BYTES (20ULL * 1024 * 1024)

static void json_string(const char *s) {
    fputc('"',result);
    for (; *s; s++) {
        if (*s=='"'||*s=='\\') fputc('\\',result);
        if ((unsigned char)*s>=32) fputc(*s,result);
    }
    fputc('"',result);
}
static int fail(const char *message) {
    fprintf(result,"{\"ok\":false,\"error\":"); json_string(message); fprintf(result,"}\n");
    if (device) LIBMTP_Release_Device(device);
    return 1;
}
static LIBMTP_file_t *named(uint32_t parent, const char *name) {
    LIBMTP_file_t *list=LIBMTP_Get_Files_And_Folders(device,storage,parent), *found=NULL;
    while (list) {
        LIBMTP_file_t *next=list->next; list->next=NULL;
        if (list->filename && !strcasecmp(list->filename,name) && !found) found=list;
        else LIBMTP_destroy_file_t(list);
        list=next;
    }
    return found;
}
static int same_file(const char *a,const char *b) {
    FILE *fa=fopen(a,"rb"), *fb=fopen(b,"rb");
    if (!fa || !fb) { if(fa)fclose(fa); if(fb)fclose(fb); return 0; }
    unsigned char ba[8192],bb[8192]; size_t na,nb; int same=1;
    do { na=fread(ba,1,sizeof ba,fa); nb=fread(bb,1,sizeof bb,fb);
        if(na!=nb || memcmp(ba,bb,na)) {same=0;break;}
    } while(na);
    if(ferror(fa)||ferror(fb))same=0;
    fclose(fa);fclose(fb);return same;
}
static int send(const char *path, uint32_t *id) {
    *id=0;
    struct stat st; if(stat(path,&st)||st.st_size<=0)return 0;
    LIBMTP_file_t *file=LIBMTP_new_file_t();
    file->filename=strdup(destination);file->parent_id=apps;
    file->storage_id=storage;file->filesize=st.st_size;file->filetype=LIBMTP_FILETYPE_UNKNOWN;
    int error=LIBMTP_Send_File_From_File(device,path,file,NULL,NULL);
    // libmtp may create an object before reporting an interrupted transfer.
    // Preserve its ID so failure handling can remove that partial object.
    *id=file->item_id;LIBMTP_destroy_file_t(file);
    return !error && *id!=0;
}
static int course_name(const char *name) {
    if(!name || !*name)return 0;
    for(const char *p=name;*p;p++)
        if((unsigned char)*p<32 || *p=='/' || *p=='\\')return 0;
    const char *extension=strrchr(name,'.');
    return extension && (!strcasecmp(extension,".fit") || !strcasecmp(extension,".gpx"));
}
static int course_file(const LIBMTP_file_t *file) {
    return file->item_id && file->filetype!=LIBMTP_FILETYPE_FOLDER && course_name(file->filename)
        && file->filesize>0 && file->filesize<=MAX_COURSE_BYTES;
}
static uint64_t unsigned_argument(const char *value, uint64_t maximum) {
    if(!value || !*value)return 0;
    for(const char *p=value;*p;p++)if(*p<'0' || *p>'9')return 0;
    errno=0;char *end=NULL;unsigned long long number=strtoull(value,&end,10);
    return errno || !end || *end || number>maximum ? 0 : number;
}
static int course_progress(uint64_t sent, uint64_t total, const void *unused) {
    (void)unused;return sent>MAX_COURSE_BYTES || total>MAX_COURSE_BYTES;
}
static int course_device_key(char output[65]) {
    char *serial=LIBMTP_Get_Serialnumber(device);
    if(!serial || !*serial || strlen(serial)>1024){free(serial);return 0;}
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(serial,(CC_LONG)strlen(serial),digest);free(serial);
    for(size_t i=0;i<sizeof digest;i++)sprintf(output+2*i,"%02x",digest[i]);
    return 1;
}
static int courses_command(int reading, char **argv, const char *target, const char *model) {
    // Only this irreversible digest enters the JSON protocol. Never print or
    // persist the hardware serial, including in errors and command arguments.
    char device_key[65];
    if(!course_device_key(device_key))return fail("无法确认这只手表的身份，请重新连接后再读取路线。");
    uint32_t requested_storage=0,requested_object=0;uint64_t expected_size=0;
    if(reading) {
        requested_storage=(uint32_t)unsigned_argument(argv[2],UINT32_MAX);
        requested_object=(uint32_t)unsigned_argument(argv[3],UINT32_MAX);
        expected_size=unsigned_argument(argv[4],MAX_COURSE_BYTES);
        if(!requested_storage || !requested_object || !expected_size || !course_name(argv[5]))
            return fail("路线选择无效，请重新读取路线列表。");
        if(strcmp(target,argv[7]))return fail("连接的手表已改变，请重新读取路线列表。");
        if(strcmp(device_key,argv[8]))return fail("连接的手表已改变，请重新读取路线列表。");
        struct stat existing;
        if(!lstat(argv[6],&existing) || errno!=ENOENT)return fail("本地路线文件已存在，请使用新的导入任务。");
    } else {
        fprintf(result,"{\"ok\":true,\"transport\":\"mtp\",\"device\":");json_string(target);
        fprintf(result,",\"name\":");json_string(model);
        fprintf(result,",\"device_key\":");json_string(device_key);fprintf(result,",\"courses\":[");
    }
    int first=1;LIBMTP_file_t *selected=NULL;
    for(LIBMTP_devicestorage_t *s=device->storage;s;s=s->next) {
        if(reading && s->id!=requested_storage)continue;
        storage=s->id;
        LIBMTP_file_t *garmin=named(LIBMTP_FILES_AND_FOLDERS_ROOT,"GARMIN");
        if(!garmin)continue;
        if(garmin->filetype!=LIBMTP_FILETYPE_FOLDER){LIBMTP_destroy_file_t(garmin);continue;}
        LIBMTP_file_t *folder=named(garmin->item_id,"COURSES");LIBMTP_destroy_file_t(garmin);
        if(!folder)continue;
        if(folder->filetype!=LIBMTP_FILETYPE_FOLDER){LIBMTP_destroy_file_t(folder);continue;}
        LIBMTP_file_t *files=LIBMTP_Get_Files_And_Folders(device,storage,folder->item_id);
        LIBMTP_destroy_file_t(folder);
        while(files) {
            LIBMTP_file_t *file=files;files=files->next;file->next=NULL;
            if(course_file(file)) {
                if(reading && file->item_id==requested_object && file->filesize==expected_size
                        && !strcmp(file->filename,argv[5]) && !selected) {
                    selected=file;continue;
                }
                if(!reading) {
                    if(!first)fputc(',',result);first=0;
                    fprintf(result,"{\"storage_id\":%u,\"object_id\":%u,\"name\":",storage,file->item_id);
                    json_string(file->filename);fprintf(result,",\"bytes\":%llu}",(unsigned long long)file->filesize);
                }
            }
            LIBMTP_destroy_file_t(file);
        }
    }
    if(!reading){fprintf(result,"]}\n");return 0;}
    if(!selected)return fail("路线已改变或不在 GARMIN/COURSES 中，请重新读取路线列表。");
    uint32_t object=selected->item_id;LIBMTP_destroy_file_t(selected);
    int error=LIBMTP_Get_File_To_File(device,object,argv[6],course_progress,NULL);
    struct stat copied;
    if(error || lstat(argv[6],&copied) || !S_ISREG(copied.st_mode) || (uint64_t)copied.st_size!=expected_size) {
        unlink(argv[6]);return fail("路线复制不完整或超过 20 MB，请重新连接后重试。");
    }
    fprintf(result,"{\"ok\":true,\"bytes\":%llu}\n",(unsigned long long)expected_size);return 0;
}
int main(int argc,char **argv) {
    // libmtp prints diagnostics to stdout. Keep the machine-readable result separate.
    result=fdopen(dup(STDOUT_FILENO),"w");dup2(STDERR_FILENO,STDOUT_FILENO);
    if(argc<2 || (strcmp(argv[1],"status") && strcmp(argv[1],"install") && strcmp(argv[1],"list-courses") && strcmp(argv[1],"read-course")))return fail("参数不正确。");
    if(!strcmp(argv[1],"status") && argc!=2)return fail("参数不正确。");
    if(!strcmp(argv[1],"list-courses") && argc!=2)return fail("参数不正确。");
    if(!strcmp(argv[1],"read-course") && argc!=9)return fail("参数不正确。");
    if(!strcmp(argv[1],"install")) {
        // install PRG expected-device backup-path verification-path [destination]
        if(argc!=6 && argc!=7)return fail("参数不正确。");
        if(argc==7)destination=argv[6];
        if(strcmp(destination,"BCTRAIL.PRG") && strcmp(destination,"BCPREVW.PRG"))
            return fail("不支持的手表程序文件名，未写入任何内容。");
    }
    LIBMTP_Init();LIBMTP_raw_device_t *raw=NULL;int count=0,selected=-1,garmins=0;
    if(LIBMTP_Detect_Raw_Devices(&raw,&count)!=LIBMTP_ERROR_NONE)return fail("未发现 USB 手表。请使用数据线连接，并选择 MTP 模式。");
    for(int i=0;i<count;i++) if(raw[i].device_entry.vendor_id==0x091e) {selected=i;garmins++;}
    if(garmins!=1){free(raw);return fail(garmins?"请只连接一只 Garmin 手表。":"未发现 Garmin 手表。");}
    device=LIBMTP_Open_Raw_Device_Uncached(&raw[selected]);free(raw);
    if(!device)return fail("无法打开手表。请退出 Garmin Express、OpenMTP 等程序后重试。");
    char *model=LIBMTP_Get_Modelname(device);
    const char *target=NULL;
    if(model && !strcasecmp(model,"Forerunner 255"))target="fr255";
    if(model && !strcasecmp(model,"Forerunner 255 Music"))target="fr255m";
    if(model && !strcasecmp(model,"Forerunner 255S"))target="fr255s";
    if(model && !strcasecmp(model,"Forerunner 255S Music"))target="fr255sm";
    if(!target){free(model);return fail("当前仅支持 Forerunner 255 系列，未写入任何内容。");}
    if(LIBMTP_Get_Storage(device,LIBMTP_STORAGE_SORTBY_NOTSORTED) || !device->storage){free(model);return fail("无法读取手表存储信息。");}
    if(!strcmp(argv[1],"list-courses") || !strcmp(argv[1],"read-course")) {
        int code=courses_command(!strcmp(argv[1],"read-course"),argv,target,model);
        free(model);if(!code)LIBMTP_Release_Device(device);return code;
    }
    uint64_t available=0;
    for(LIBMTP_devicestorage_t *s=device->storage;s;s=s->next) {
        storage=s->id;
        LIBMTP_file_t *folder=named(LIBMTP_FILES_AND_FOLDERS_ROOT,"GARMIN");
        if(!folder)continue;
        LIBMTP_file_t *appfolder=named(folder->item_id,"APPS");LIBMTP_destroy_file_t(folder);
        if(appfolder){apps=appfolder->item_id;available=s->FreeSpaceInBytes;LIBMTP_destroy_file_t(appfolder);break;}
    }
    if(!apps){free(model);return fail("没有找到 GARMIN/APPS 目录，未写入任何内容。");}
    LIBMTP_file_t *old=named(apps,destination);
    if(!strcmp(argv[1],"status")) {
        fprintf(result,"{\"ok\":true,\"transport\":\"mtp\",\"device\":");json_string(target);
        fprintf(result,",\"name\":");json_string(model);
        fprintf(result,",\"free_bytes\":%llu,\"installed\":%s}\n",(unsigned long long)available,old?"true":"false");
        if(old)LIBMTP_destroy_file_t(old);free(model);LIBMTP_Release_Device(device);return 0;
    }
    if(strcmp(argv[3],target)){free(model);return fail("编译型号与连接的手表不匹配，未安装。");}
    free(model);struct stat st;
    if(stat(argv[2],&st) || st.st_size<64 || st.st_size>16*1024*1024)return fail("程序文件大小不正确。");
    if(available<(uint64_t)st.st_size+1024*1024)return fail("手表剩余空间不足，旧地图已保留。");
    if(access(argv[4],F_OK)==0 || access(argv[5],F_OK)==0)return fail("备份文件已存在，请使用新的安装任务。");
    int had_old=old!=NULL;
    if(old) {
        if(LIBMTP_Get_File_To_File(device,old->item_id,argv[4],NULL,NULL))return fail("无法备份旧程序，已停止安装。");
        struct stat backup;
        if(stat(argv[4],&backup)||(uint64_t)backup.st_size!=old->filesize)return fail("旧程序备份不完整，已停止安装。");
        if(LIBMTP_Delete_Object(device,old->item_id))return fail("无法替换旧程序，备份保留在 Mac。");
        LIBMTP_destroy_file_t(old);
    }
    uint32_t id=0;
    int sent=send(argv[2],&id);
    int verified=sent && !LIBMTP_Get_File_To_File(device,id,argv[5],NULL,NULL) && same_file(argv[2],argv[5]);
    if(!verified) {
        if(id) {
            if(LIBMTP_Delete_Object(device,id))
                return fail("安装失败，未能清理未通过校验的程序，自动恢复未完成。请保留 Mac 上的备份。");
        } else {
            // Without an object ID, a failed upload may have left an object we
            // cannot identify. Do not create a duplicate or claim restoration.
            return fail("安装失败，无法确认未完成传输的文件状态，自动恢复未完成。请保留 Mac 上的备份。");
        }
        if(had_old) {
            uint32_t restored=0;
            if(!send(argv[4],&restored) || LIBMTP_Get_File_To_File(device,restored,argv[5],NULL,NULL) || !same_file(argv[4],argv[5]))
                return fail("安装失败且自动恢复未完成。旧程序备份仍在 Mac，请勿删除备份。");
        }
        return fail(had_old?"安装校验失败，旧程序已恢复。":"安装校验失败，请重新连接后重试。");
    }
    fprintf(result,"{\"ok\":true,\"status\":\"copied_verified\",\"replaced\":%s,\"hardware_verified\":false}\n",had_old?"true":"false");
    LIBMTP_Release_Device(device);return 0;
}
