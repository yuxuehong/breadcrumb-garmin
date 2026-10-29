import Toybox.Test;
import Toybox.Application;
import Toybox.Math;
import Toybox.Graphics;

function freshCamera() {
    Application.Storage.deleteValue("camera");
    return new TrailMapState();
}

(:test)
function distantGpsKeepsRaceVisible(logger) {
    var map = freshCamera();
    map.updateGps(-100000.0d,-100000.0d,0);
    map.prepare(260,260);
    Test.assert(!map.isTracking());
    Test.assert(map.x >= OfflineMap.MIN_X && map.x <= OfflineMap.MAX_X);
    Test.assert(map.y >= OfflineMap.MIN_Y && map.y <= OfflineMap.MAX_Y);
    return true;
}

(:test)
function gpsCannotMoveLockedPreview(logger) {
    var map = freshCamera();
    map.beginPreview(); map.fit(260,260); map.pan(1,0);
    var x=map.x, y=map.y, zoom=map.zoom;
    map.updateGps(OfflineMap.START_X,OfflineMap.START_Y,0);
    map.prepare(260,260);
    Test.assertEqual(x,map.x); Test.assertEqual(y,map.y); Test.assertEqual(zoom,map.zoom);
    Test.assert(!map.isTracking());
    return true;
}

(:test)
function followReturnsToActualGps(logger) {
    var map = freshCamera();
    map.beginPreview(); map.fit(260,260);
    map.updateGps(OfflineMap.START_X,OfflineMap.START_Y,0);
    map.followLocation();
    Test.assert(map.isTracking());
    Test.assertEqual(map.x,OfflineMap.START_X); Test.assertEqual(map.y,OfflineMap.START_Y);
    Test.assertEqual(map.zoom,OfflineMap.ZOOM);
    return true;
}

(:test)
function runningOutsideMapNeverPretendsToBeAtStart(logger) {
    var map = freshCamera(); map.recording = true;
    map.updateGps(-10000.0d,-20000.0d,0); map.prepare(260,260);
    Test.assert(map.isTracking()); Test.assert(!map.inside);
    Test.assertEqual(map.x,-10000.0d); Test.assertEqual(map.y,-20000.0d);
    return true;
}

(:test)
function zoomPreservesCameraAndStopsAtLimits(logger) {
    var map = freshCamera(); map.startPoint();
    var x=map.x, y=map.y;
    for(var i=0;i<30;i++){map.changeZoom(-1);}
    Test.assertEqual(map.zoom,OfflineMap.MIN_ZOOM);
    for(var i=0;i<30;i++){map.changeZoom(1);}
    Test.assertEqual(map.zoom,OfflineMap.ZOOM);
    Test.assertEqual(map.x,x); Test.assertEqual(map.y,y);
    return true;
}

(:test)
function panStopsAtMapBoundary(logger) {
    var map=freshCamera(); map.startPoint();
    map.pan(-100000,-100000);
    Test.assertEqual(map.x,0); Test.assertEqual(map.y,0);
    map.pan(100000,100000);
    Test.assertEqual(map.x,OfflineMap.MAP_WIDTH-1); Test.assertEqual(map.y,OfflineMap.MAP_HEIGHT-1);
    return true;
}

(:test)
function previewSurvivesRestartButNotAnotherRace(logger) {
    var map=freshCamera(); map.beginPreview(); map.fit(260,260); map.pan(1,0); map.save();
    var restored=new TrailMapState();
    Test.assertMessage(!restored.follow,"Preview mode should survive restart");
    Test.assertEqualMessage(restored.x,map.x,"Camera center should survive restart");
    Test.assertEqualMessage(restored.zoom,map.zoom,"Zoom should survive restart");
    Application.Storage.setValue("camera",{"route"=>"another-race","follow"=>false,"x"=>-999,"y"=>-999,"zoom"=>0});
    var reset=new TrailMapState();
    Test.assert(reset.follow); Test.assertEqual(reset.x,OfflineMap.START_X);
    Application.Storage.deleteValue("camera");
    return true;
}

class PreviewTestCanvas {
    var count=0;
    function getWidth() { return 260; }
    function getHeight() { return 260; }
    function setColor(a,b) {}
    function clear() {}
    function setPenWidth(width) {}
    function drawLine(a,b,c,d) {}
    function fillCircle(a,b,c) {}
    function fillRectangle(a,b,c,d) {}
    function drawText(a,b,c,d,e) {}
    function getTextWidthInPixels(text,font) { return text.length()*7; }
    function drawBitmap(x,y,bitmap) {
        Test.assertEqual(bitmap.getWidth(),256);
        Test.assertEqual(bitmap.getHeight(),256);
        Test.assert(x < 260 && y < 260 && x > -256 && y > -256);
        count++;
    }
}

(:test)
function allPreviewLevelsLoadRealBitmapResources(logger) {
    var map=freshCamera(); map.startPoint();
    var renderer=new TrailRenderer(map);
    for(var z=OfflineMap.MIN_ZOOM;z<=OfflineMap.ZOOM;z++) {
        map.zoom=z;
        var dc=new PreviewTestCanvas();
        renderer.draw(dc,0);
        Test.assert(dc.count > 0 && dc.count <= 9);
    }
    renderer.release();
    return true;
}
