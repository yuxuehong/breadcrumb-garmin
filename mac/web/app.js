'use strict';
const $=id=>document.getElementById(id);
const token=location.hash.slice(1)||sessionStorage.getItem('trailToken')||'';
sessionStorage.setItem('trailToken',token);history.replaceState(null,'',location.pathname);
let state={},plan=null,identity=null,images=new Map(),view={x:0,y:0,scale:1},drag=null,poll=null,loading=false;
let pending=false,pendingKind='',cancelPending=false,refreshing=false,requestVersion=0;
let coursesOpen=null,selectedCourse=null,courseListKey='',courseOptions=null;
const canvas=$('map'),ctx=canvas.getContext('2d');
async function api(path,data){const response=await fetch('/api/'+path,{method:data===undefined?'GET':'POST',headers:{'X-Trail-Token':token,'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)});const result=await response.json();if(!response.ok)throw Error(result.error||'操作未完成');return result;}
function error(e){$('status').textContent=e.message;$('status').classList.add('error');}
function busy(){return pending||!!state.job?.active;}
function selectedOptions(){return {zoom:Number($('zoom').value),buffer:Number($('buffer').value)};}
function editingCourse(){return !!coursesOpen&&courseOptions!==null&&state.courses?.status==='ready'&&state.courses.items.length>0;}
function displayOptions(){const options=editingCourse()?courseOptions:plan?{zoom:plan.zooms[0],buffer:plan.buffer_m}:null;if(options){$('zoom').value=options.zoom;$('buffer').value=options.buffer;}}
function courseSize(bytes){return Number.isFinite(bytes)&&bytes>=0?(bytes<1024?bytes+' B':bytes<1024*1024?(bytes/1024).toFixed(1)+' KB':(bytes/1024/1024).toFixed(1)+' MB'):'';}
function renderCourses(){
  const courses=state.courses||{status:'idle',items:[]},items=Array.isArray(courses.items)?courses.items:[];
  if(coursesOpen===null)coursesOpen=courses.status!=='idle';
  if(coursesOpen&&courses.status==='ready'&&courseOptions===null)courseOptions=selectedOptions();
  const scanning=(pending&&pendingKind==='courses')||(state.job?.active&&state.job.kind==='courses')||courses.status==='loading';
  const importing=(pending&&pendingKind==='import-course')||(state.job?.active&&state.job.kind==='import-course');
  $('courses').hidden=!coursesOpen;$('watchImport').setAttribute('aria-expanded',String(!!coursesOpen));
  $('watchImport').textContent=scanning?'正在读取路线…':'从手表导入';
  $('courseStatus').textContent=scanning?'正在读取手表中的路线，请保持 USB 连接…':importing?'正在导入所选路线，请保持 USB 连接…':courses.status==='error'?(courses.error||'路线读取未完成，请检查连接后重新读取。'):courses.status==='ready'?(items.length?(courses.device?courses.device+' · ':'')+items.length+' 条路线，选择一条导入。':'手表中还没有可导入的路线。可先同步路线到手表，再重新读取；也可直接拖入 GPX。'):'连接手表后，点击“重新读取”。';
  $('courseStatus').classList.toggle('error',courses.status==='error'&&!scanning&&!importing);
  const visibleItems=courses.status==='ready'?items:[],key=JSON.stringify(visibleItems);
  if(key!==courseListKey){
    courseListKey=key;
    if(!visibleItems.some(item=>item.id===selectedCourse))selectedCourse=null;
    $('courseList').replaceChildren();
    for(const item of visibleItems){
      const label=document.createElement('label'),radio=document.createElement('input'),copy=document.createElement('span'),name=document.createElement('span'),meta=document.createElement('span');
      label.className='courseOption';radio.type='radio';radio.name='watchCourse';radio.value=item.id;radio.checked=item.id===selectedCourse;
      copy.className='courseCopy';name.className='courseName';name.textContent=item.name||'未命名路线';meta.className='courseMeta';meta.textContent=[String(item.format||'').toUpperCase(),courseSize(item.bytes)].filter(Boolean).join(' · ');
      radio.onchange=()=>{if(!busy()){selectedCourse=item.id;updateControls();}};
      copy.append(name,meta);label.append(radio,copy);$('courseList').append(label);
    }
  }
  $('courseChoices').hidden=!visibleItems.length;
  $('courseChoices').disabled=busy();$('courseImport').disabled=busy()||courses.status!=='ready'||!visibleItems.some(item=>item.id===selectedCourse);
  $('courseImport').textContent=importing?'正在导入…':'导入路线';$('coursesRefresh').disabled=busy();$('coursesClose').disabled=busy();
}
function updateControls(){
  const job=state.job||{},active=!!job.active,locked=busy(),kind=pending?pendingKind:job.kind,usb=kind==='courses'||kind==='import-course';
  renderCourses();
  const mapLocked=locked||editingCourse();
  $('primary').disabled=!plan||mapLocked;$('build').disabled=!plan||mapLocked;$('install').disabled=!state.ready||mapLocked;
  $('connect').disabled=locked;$('options').disabled=locked;$('target').disabled=locked;$('watchImport').disabled=locked;$('file').disabled=locked;$('drop').setAttribute('aria-disabled',String(locked));
  $('cancel').hidden=!active||job.stage==='install';$('cancel').disabled=pending||cancelPending;
  $('cancel').textContent=cancelPending?'正在取消…':usb?(kind==='courses'?'取消读取':'取消导入'):'暂停，保留下载进度';
  $('primary').innerHTML=active?(usb?(kind==='courses'?'正在读取路线…':'正在导入路线…'):job.stage==='install'?'正在安装地图…':'正在准备地图…'):editingCourse()?'先导入所选路线':state.ready?'安装到 255 <span>→</span>':job.stage==='download_done'?'确认预览，生成手表地图 <span>→</span>':'下载并预览地图 <span>→</span>';
  for(const button of $('history').querySelectorAll('button'))button.disabled=locked;
}
async function perform(task,message,kind=''){
  if(busy())return;
  pending=true;pendingKind=kind;requestVersion++;updateControls();
  if(message){$('status').textContent=message;$('status').classList.remove('error');}
  let failure;
  try{render(await task());if(!state.job?.active)loadTiles().catch(error);}
  catch(e){failure=e;}
  finally{pending=false;pendingKind='';updateControls();if(failure)error(failure);}
}
function project(p){const lat=p[0]*Math.PI/180;return[(p[1]+180)/360,(1-Math.asinh(Math.tan(lat))/Math.PI)/2];}
function dimensions(){const rect=canvas.getBoundingClientRect();return[rect.width,rect.height];}
function fit(){if(!plan)return;const factor=256*2**plan.zooms[0];let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;for(const segment of plan.route.segments)for(const point of segment){const p=project(point),x=p[0]*factor,y=p[1]*factor;minX=Math.min(minX,x);maxX=Math.max(maxX,x);minY=Math.min(minY,y);maxY=Math.max(maxY,y);}const[w,h]=dimensions();view={x:(minX+maxX)/2,y:(minY+maxY)/2,scale:Math.min(2,(w-90)/Math.max(100,maxX-minX),(h-90)/Math.max(100,maxY-minY))};draw();}
function draw(){const[w,h]=dimensions(),dpr=devicePixelRatio||1;canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);ctx.setTransform(dpr,0,0,dpr,0,0);ctx.fillStyle='#e8eddf';ctx.fillRect(0,0,w,h);if(!plan)return;const sx=x=>(x-view.x)*view.scale+w/2,sy=y=>(y-view.y)*view.scale+h/2;
const level=Math.max(plan.preview_min_zoom??plan.zooms[0],Math.min(plan.zooms[0],plan.zooms[0]+Math.floor(Math.log2(view.scale)))),tileScale=2**(plan.zooms[0]-level);
for(const tile of plan.tiles){if(tile[0]!==level)continue;const key=tile.join('/'),im=images.get(key),x=sx(tile[1]*256*tileScale),y=sy(tile[2]*256*tileScale),size=256*tileScale*view.scale;if(im){ctx.drawImage(im,x,y,size+.3,size+.3);}else{ctx.fillStyle='#dce7d2';ctx.fillRect(x,y,size,size);ctx.strokeStyle='#b2c4ab';ctx.lineWidth=.6;ctx.strokeRect(x,y,size,size);}}
// GPX is kept locally and its segments remain separate.
const factor=256*2**plan.zooms[0];for(const segment of plan.route.segments){ctx.beginPath();segment.forEach((p,i)=>{const q=project(p);i?ctx.lineTo(sx(q[0]*factor),sy(q[1]*factor)):ctx.moveTo(sx(q[0]*factor),sy(q[1]*factor));});ctx.strokeStyle='#fff';ctx.lineWidth=5;ctx.lineJoin='round';ctx.stroke();ctx.strokeStyle='#0041d2';ctx.lineWidth=2.6;ctx.stroke();}
const start=project(plan.route.segments[0][0]);ctx.beginPath();ctx.arc(sx(start[0]*factor),sy(start[1]*factor),5,0,Math.PI*2);ctx.fillStyle='#1a9152';ctx.fill();ctx.strokeStyle='#fff';ctx.lineWidth=2;ctx.stroke();ctx.fillStyle='#23412f';ctx.font='12px -apple-system';ctx.fillText('N ↑',w-45,28);}
async function loadTiles(){
  if(!plan||loading)return;
  loading=true;const current=identity,tiles=plan.tiles;
  try{for(const tile of tiles){if(identity!==current)break;const key=tile.join('/');if(images.has(key))continue;const r=await fetch('/api/tile/'+current+'/'+key,{headers:{'X-Trail-Token':token}});if(!r.ok)continue;const blob=await r.blob();const im=await createImageBitmap(blob);if(identity===current){images.set(key,im);draw();}else im.close();}}
  catch(e){if(identity===current)throw e;}
  finally{loading=false;if(identity!==current&&plan)loadTiles().catch(error);}
}
function render(value){state=value;const changed=value.id!==identity;if(changed){for(const image of images.values())image.close();images.clear();identity=value.id;plan=value.plan||null;if(coursesOpen!==null){coursesOpen=false;courseOptions=null;}}else plan=value.plan||null;
const job=value.job||{},active=!!job.active;$('status').textContent=job.message||'';$('status').classList.toggle('error',job.stage==='error');$('progress').hidden=!active;$('progress').value=job.progress||0;$('next').hidden=!value.ready;
if(plan){$('routeName').textContent=plan.route.name;$('routeHint').textContent='点击可更换 GPX 文件';$('mapTitle').textContent=plan.route.name;$('distance').textContent=(plan.distance_m/1000).toFixed(1)+' km';$('tileCount').textContent=plan.tiles.length+' 张';$('stats').hidden=false;$('empty').hidden=true;$('mapBadge').hidden=false;$('step2').classList.add('current');$('step3').classList.toggle('current',value.ready);if(changed)fit();}displayOptions();
if(value.device){$('deviceName').textContent=value.device.name+' · 已识别';$('deviceDot').classList.add('online');$('target').value=value.device.device;}else{$('deviceName').textContent='连接你的 Forerunner 255';$('deviceDot').classList.remove('online');}
$('sdk').textContent=value.sdk?'手表构建环境已就绪。':'首次使用请先安装 Garmin Connect IQ SDK 和 255 设备文件。';$('history').replaceChildren();for(const item of value.history||[]){const b=document.createElement('button');b.textContent=(item.id===identity?'✓ ':'')+item.name+(item.ready?' · 已生成':'');b.onclick=()=>perform(()=>api('select',{id:item.id}),'正在打开保存的比赛…');$('history').append(b);}updateControls();if(active&&!poll)poll=setInterval(refresh,1300);if(!active&&poll){clearInterval(poll);poll=null;loadTiles().catch(error);}draw();}
async function refresh(){if(pending||cancelPending||refreshing)return;refreshing=true;const version=requestVersion;try{const value=await api('state');if(version===requestVersion){render(value);if(state.job?.active&&state.job.stage==='download')loadTiles().catch(error);}}catch(e){if(version===requestVersion)error(e);}finally{refreshing=false;}}
async function importFile(file){if(!file||busy())return;if(!file.name.toLowerCase().endsWith('.gpx'))return error(Error('请选择 .gpx 格式的比赛路线。'));if(file.size>20*1024*1024)return error(Error('文件过大，请按单场比赛导出。'));const options={zoom:Number($('zoom').value),buffer:Number($('buffer').value)};await perform(async()=>{const bytes=new Uint8Array(await file.arrayBuffer());let s='';for(let i=0;i<bytes.length;i+=8192)s+=String.fromCharCode(...bytes.subarray(i,i+8192));return api('import',{gpx:btoa(s),filename:file.name,...options});},'正在导入 GPX…');}
$('drop').onclick=()=>{if(!busy())$('file').click();};$('drop').onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();$('drop').click();}};$('file').onchange=e=>{importFile(e.target.files[0]);e.target.value='';};$('drop').ondragover=e=>{e.preventDefault();if(!busy())$('drop').classList.add('over');};$('drop').ondragleave=()=>$('drop').classList.remove('over');$('drop').ondrop=e=>{e.preventDefault();$('drop').classList.remove('over');importFile(e.dataTransfer.files[0]);};
function scanCourses(){if(busy())return;coursesOpen=true;selectedCourse=null;courseListKey='';courseOptions=selectedOptions();return perform(()=>api('courses',{}),'正在读取手表中的路线…','courses');}
$('watchImport').onclick=scanCourses;$('coursesRefresh').onclick=scanCourses;
$('coursesClose').onclick=()=>{if(!busy()){coursesOpen=false;courseOptions=null;displayOptions();updateControls();}};
$('courseImport').onclick=()=>{if(busy()||state.courses?.status!=='ready'||!state.courses.items.some(item=>item.id===selectedCourse))return;const payload={id:selectedCourse,zoom:Number($('zoom').value),buffer:Number($('buffer').value)};perform(()=>api('import-course',payload),'正在导入所选路线…','import-course');};
for(const id of ['zoom','buffer'])$(id).onchange=()=>{if(busy())return;const payload=selectedOptions();if(editingCourse()){courseOptions=payload;return;}if(plan)perform(()=>api('options',payload),'正在调整地图范围…');};
function start(action){if(editingCourse())return;const payload={action,device:$('target').value};return perform(()=>api('start',payload),'正在准备…');}
$('primary').onclick=()=>start(state.ready?'install':state.job?.stage==='download_done'?'build':'download');$('build').onclick=()=>start('build');$('install').onclick=()=>start('install');
$('cancel').onclick=async()=>{if(pending||cancelPending||!state.job?.active||state.job.stage==='install')return;cancelPending=true;requestVersion++;updateControls();try{render(await api('cancel',{}));}catch(e){error(e);}finally{cancelPending=false;updateControls();}};
$('connect').onclick=()=>perform(async()=>{try{return await api('device',{});}catch(e){$('deviceName').textContent='未连接手表';$('deviceDot').classList.remove('online');throw e;}},'正在检测手表…');
$('fit').onclick=fit;$('plus').onclick=()=>{view.scale=Math.min(5,view.scale*1.4);draw();};$('minus').onclick=()=>{view.scale=Math.max(.03,view.scale/1.4);draw();};canvas.onpointerdown=e=>{drag={x:e.clientX,y:e.clientY,vx:view.x,vy:view.y};canvas.setPointerCapture(e.pointerId);};canvas.onpointermove=e=>{if(drag){view.x=drag.vx-(e.clientX-drag.x)/view.scale;view.y=drag.vy-(e.clientY-drag.y)/view.scale;draw();}};canvas.onpointerup=()=>drag=null;canvas.onpointercancel=()=>drag=null;canvas.onwheel=e=>{e.preventDefault();view.scale=Math.max(.03,Math.min(5,view.scale*Math.exp(-e.deltaY*.002)));draw();};new ResizeObserver(draw).observe(canvas);refresh().then(loadTiles);
