import Toybox.Test;
import Toybox.Activity;
import Toybox.Math;

function navigationCamera() {
    var map=freshCamera();
    map.recording=true; map.running=true;
    // A small course centred on the real package start makes motion tests
    // independent of whichever race was used to generate this test program.
    map.navigation=[OfflineMap.START_X-20,OfflineMap.START_Y,
                    OfflineMap.START_X+20,OfflineMap.START_Y];
    return map;
}

function updateAtCourseStart(map,speed,quality) {
    map.updateMotion(OfflineMap.START_X,OfflineMap.START_Y,0,speed,quality);
}

(:test)
function firstUsableFixShowsCloseMapAtActualPosition(logger) {
    var map=navigationCamera();
    updateAtCourseStart(map,1.4,4);
    map.prepare(260,260);
    Test.assert(map.isTracking());
    Test.assertEqual(map.x,OfflineMap.START_X);
    Test.assertEqual(map.y,OfflineMap.START_Y);
    Test.assertEqual(map.zoom,map.maxZoom());
    // North is zero radians, not an absent direction.
    Test.assertEqual(map.heading,0);
    return true;
}

(:test)
function briefSpeedChangesDoNotMakeMapZoomFlicker(logger) {
    var map=navigationCamera();
    updateAtCourseStart(map,1.4,4);
    var closeZoom=map.zoom;
    for(var i=0;i<30;i++) {
        updateAtCourseStart(map,i%2==0 ? 4.5 : 1.4,4);
        Test.assertEqual(map.zoom,closeZoom);
    }
    for(var j=0;j<20;j++) { updateAtCourseStart(map,4.5,4); }
    Test.assert(map.zoom<closeZoom);
    var wideZoom=map.zoom;
    for(var k=0;k<3;k++) { updateAtCourseStart(map,1.4,4); }
    Test.assertEqual(map.zoom,wideZoom);
    for(var n=0;n<20;n++) { updateAtCourseStart(map,1.4,4); }
    Test.assertEqual(map.zoom,closeZoom);
    return true;
}

(:test)
function movingAwayWidensViewWithoutMovingTrueGps(logger) {
    var map=navigationCamera();
    updateAtCourseStart(map,1.4,4);
    var closeZoom=map.zoom;
    var px=OfflineMap.START_X,py=OfflineMap.START_Y+100;
    for(var i=0;i<20;i++) { map.updateMotion(px,py,Math.PI,1.4,4); }
    map.prepare(260,260);
    Test.assert(map.zoom<closeZoom);
    Test.assertEqual(map.x,px); Test.assertEqual(map.y,py);
    Test.assertEqual(map.gpsX,px); Test.assertEqual(map.gpsY,py);
    // Both the user and the nearest route must fit comfortably inside the
    // circular display; widening must not snap the GPS marker to the route.
    var routePixels=map.nearestRouteDistance(px,py)*Math.pow(2,map.zoom-OfflineMap.ZOOM);
    Test.assert(routePixels<100);
    return true;
}

(:test)
function noFixAndLastKnownFixCannotPretendToBeLive(logger) {
    var map=navigationCamera();
    updateAtCourseStart(map,1.4,4);
    var x=map.x,y=map.y,zoom=map.zoom;
    var qualities=[0,1,null];
    for(var i=0;i<qualities.size();i++) {
        map.updateMotion(x+500,y+500,Math.PI,12,qualities[i]);
        map.prepare(260,260);
        Test.assert(!map.isTracking());
        Test.assert(map.gpsX==null); Test.assert(map.gpsY==null);
        Test.assertEqual(map.x,x); Test.assertEqual(map.y,y);
        Test.assertEqual(map.zoom,zoom);
        Test.assert(map.recording && map.running);
    }
    updateAtCourseStart(map,1.4,4);
    Test.assert(map.isTracking());
    return true;
}

(:test)
function poorGpsCanUpdatePositionButCannotTriggerZoom(logger) {
    var map=navigationCamera();
    updateAtCourseStart(map,1.4,4);
    var zoom=map.zoom;
    var px=OfflineMap.START_X+40,py=OfflineMap.START_Y;
    for(var i=0;i<30;i++) { map.updateMotion(px,py,null,15,2); }
    Test.assertEqual(map.zoom,zoom);
    Test.assertEqual(map.gpsX,px); Test.assertEqual(map.gpsY,py);
    Test.assert(map.heading==null);
    Test.assert(map.pendingZoom==null);
    return true;
}

(:test)
function pausedRecordingRetainsScaleAndRealLocationOutsideMap(logger) {
    var map=navigationCamera();
    updateAtCourseStart(map,1.4,4);
    var zoom=map.zoom;
    map.running=false;
    for(var i=0;i<20;i++) { map.updateMotion(-10000.0d,-20000.0d,0,15,4); }
    map.prepare(260,260);
    Test.assert(map.recording && !map.running);
    Test.assert(map.isTracking() && !map.inside);
    Test.assertEqual(map.zoom,zoom);
    Test.assertEqual(map.x,-10000.0d); Test.assertEqual(map.y,-20000.0d);
    var field=new TrailField(map,new TrailRenderer(map));
    field.onTimerStart();
    Test.assert(map.recording && map.running && map.follow);
    Test.assertEqual(map.zoom,zoom);
    updateAtCourseStart(map,1.4,4);
    Test.assertEqual(map.x,OfflineMap.START_X);
    Test.assertEqual(map.y,OfflineMap.START_Y);
    return true;
}

(:test)
function nearestRouteDoesNotBridgeDisconnectedTracks(logger) {
    var map=navigationCamera();
    map.navigation=[0.0d,0.0d,10.0d,0.0d,null,null,90.0d,0.0d,100.0d,0.0d];
    Test.assertEqual(map.nearestRouteDistance(50.0d,0.0d),40.0d);
    Test.assertEqual(map.nearestRouteDistance(5.0d,3.0d),3.0d);
    map.navigation=[5.0d,5.0d,5.0d,5.0d,null,null,100.0d,100.0d];
    Test.assertEqual(map.nearestRouteDistance(5.0d,8.0d),3.0d);
    return true;
}

(:test)
function distantPrestartFixDoesNotDelayFirstLocalCloseView(logger) {
    var map=navigationCamera();
    map.recording=false; map.running=false;
    map.updateMotion(-10000.0d,-20000.0d,0,1.4,4);
    map.prepare(260,260);
    Test.assert(!map.navigationReady);
    Test.assert(!map.isTracking());
    updateAtCourseStart(map,1.4,4);
    map.prepare(260,260);
    Test.assert(map.isTracking());
    Test.assertEqual(map.zoom,map.maxZoom());
    return true;
}

(:test)
function prestartGpsRecoveryImmediatelyReturnsFromOverview(logger) {
    var map=navigationCamera(); map.recording=false; map.running=false;
    updateAtCourseStart(map,1.4,4);
    map.updateMotion(null,null,null,null,0); map.prepare(260,260);
    Test.assert(!map.navigationReady);
    updateAtCourseStart(map,1.4,4); map.prepare(260,260);
    Test.assertEqual(map.zoom,map.maxZoom());
    Test.assertEqual(map.x,OfflineMap.START_X);
    return true;
}

(:test)
function liveMotionCannotMoveIndependentRacePreview(logger) {
    var map=navigationCamera();
    map.standalone=true; map.recording=false; map.running=false;
    map.startPoint(); map.pan(1,0);
    var x=map.x,y=map.y,zoom=map.zoom;
    for(var i=0;i<30;i++) { updateAtCourseStart(map,12,4); }
    Test.assertEqual(map.x,x); Test.assertEqual(map.y,y);
    Test.assertEqual(map.zoom,zoom);
    Test.assert(!map.follow && !map.isTracking());
    return true;
}

(:test)
function dataFieldReadsActivityWithoutChangingNativeMetrics(logger) {
    var map=navigationCamera();
    var field=new TrailField(map,new TrailRenderer(map));
    var info=Activity.getActivityInfo();
    var state=info.timerState,heart=info.currentHeartRate,distance=info.elapsedDistance,time=info.timerTime;
    field.compute(info);
    Test.assert(info.timerState==state);
    Test.assert(info.currentHeartRate==heart);
    Test.assert(info.elapsedDistance==distance);
    Test.assert(info.timerTime==time);
    Test.assertEqual(map.running,state==Activity.TIMER_STATE_ON);
    Test.assertEqual(map.recording,state==Activity.TIMER_STATE_ON || state==Activity.TIMER_STATE_PAUSED || state==Activity.TIMER_STATE_STOPPED);
    return true;
}
