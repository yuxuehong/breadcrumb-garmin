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
    var bounds=OfflineMap.extent(map.zoom);
    map.pan(-100000,-100000);
    Test.assertEqual(map.x,bounds[0]+130.0d); Test.assertEqual(map.y,bounds[1]+130.0d);
    map.pan(100000,100000);
    Test.assertEqual(map.x,bounds[2]-130.0d); Test.assertEqual(map.y,bounds[3]-130.0d);
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
    var width=260;
    var height=260;
    var rectangles=[];
    var textCount=0;
    var lineCount=0;
    var lastText=null;
    var markerX=null;
    var markerY=null;
    function getWidth() { return width; }
    function getHeight() { return height; }
    function setColor(a,b) {}
    function clear() {}
    function setPenWidth(width) {}
    function drawLine(a,b,c,d) { lineCount++; }
    function fillCircle(a,b,c) { markerX=a; markerY=b; }
    function fillRectangle(a,b,c,d) {}
    function drawText(a,b,c,d,e) { textCount++; lastText=d; }
    function getTextWidthInPixels(text,font) { return text.length()*7; }
    function drawBitmap(x,y,bitmap) {
        Test.assertEqual(bitmap.getWidth(),256);
        Test.assertEqual(bitmap.getHeight(),256);
        Test.assert(x < width && y < height && x > -256 && y > -256);
        rectangles.add([x,y,x+256,y+256]);
        count++;
    }
    function assertFullCoverage() {
        // Check every scanline against the union of actual bitmap draws.
        // This catches thin blank strips that a few sample pixels miss.
        for(var y=0;y<height;y++) {
            var cursor=0;
            while(cursor<width) {
                var edge=cursor;
                for(var i=0;i<rectangles.size();i++) {
                    var r=rectangles[i];
                    if(r[1]<=y && r[3]>y && r[0]<=cursor && r[2]>edge) { edge=r[2]; }
                }
                Test.assertMessage(edge>cursor,"Downloaded tiles must cover the whole viewport");
                cursor=edge;
            }
        }
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
        dc.assertFullCoverage();
        Test.assertEqual(dc.textCount,0); Test.assertEqual(dc.lineCount,0);
    }
    renderer.release();
    return true;
}

(:test)
function overviewFitsEveryCourseCornerInsideRoundScreen(logger) {
    var map=freshCamera(); map.beginPreview();
    var sizes=[218,260];
    for(var s=0;s<sizes.size();s++) {
        var size=sizes[s]; map.fit(size,size);
        var scale=Math.pow(2,map.zoom-OfflineMap.ZOOM);
        var xs=[OfflineMap.MIN_X,OfflineMap.MAX_X];
        var ys=[OfflineMap.MIN_Y,OfflineMap.MAX_Y];
        for(var i=0;i<2;i++) {
            for(var j=0;j<2;j++) {
                var dx=(xs[i]-map.x)*scale,dy=(ys[j]-map.y)*scale;
                Test.assertMessage(Math.sqrt(dx*dx+dy*dy)<=size/2.0d-12+0.001,
                    "Every course corner must fit inside the round bezel");
            }
        }
    }
    return true;
}

(:test)
function everyZoomAndPanExtremeFillsBothWatchSizes(logger) {
    var map=freshCamera(); map.beginPreview();
    var renderer=new TrailRenderer(map);
    var sizes=[218,260];
    var directions=[[-1,-1],[1,-1],[-1,1],[1,1]];
    for(var s=0;s<sizes.size();s++) {
        map.prepare(sizes[s],sizes[s]);
        for(var z=OfflineMap.MIN_ZOOM;z<=OfflineMap.ZOOM;z++) {
            map.zoom=z;
            for(var i=0;i<directions.size();i++) {
                map.pan(directions[i][0]*1000000,directions[i][1]*1000000);
                var dc=new PreviewTestCanvas(); dc.width=sizes[s]; dc.height=sizes[s];
                renderer.draw(dc,0); dc.assertFullCoverage();
                Test.assertEqual(dc.textCount,0); Test.assertEqual(dc.lineCount,0);
            }
        }
    }
    renderer.release(); return true;
}

(:test)
function outsideTrackingShowsWarningAtActualPosition(logger) {
    var map=freshCamera(); map.recording=true;
    var bounds=OfflineMap.extent(OfflineMap.ZOOM);
    var x=bounds[0]-10000,y=bounds[1]-20000;
    map.updateGps(x,y,null);
    var renderer=new TrailRenderer(map); var dc=new PreviewTestCanvas();
    renderer.draw(dc,null);
    Test.assertEqual(map.x,x); Test.assertEqual(map.y,y);
    Test.assertEqual(dc.textCount,1);
    Test.assert(dc.lastText.equals(trailText(Rez.Strings.Outside)));
    Test.assertEqual(dc.markerX,130.0d); Test.assertEqual(dc.markerY,130.0d);
    renderer.release(); return true;
}

(:test)
function buttonsZoomAndPanWithoutActivityOrGps(logger) {
    var map=freshCamera(); map.standalone=true;
    var view=new TrailPreview(map,new TrailRenderer(map));
    var input=new TrailPreviewDelegate(view);
    map.fit(260,260); view.firstFit=false;
    Test.assert(!map.follow && !map.recording && map.gpsX == null);
    map.zoom=OfflineMap.MIN_ZOOM; var oldZoom=map.zoom;
    Test.assert(input.onPreviousPage());
    Test.assertEqual(map.zoom,trailMin(oldZoom+1,OfflineMap.ZOOM));
    Test.assert(input.onNextPage());
    Test.assertEqual(map.zoom,oldZoom);
    map.zoom=OfflineMap.ZOOM;
    input.onSelect(); Test.assertEqual(view.control,1);
    map.y=OfflineMap.MAP_HEIGHT/2.0d; var y=map.y;
    input.onPreviousPage(); Test.assert(map.y < y);
    input.onNextPage(); Test.assertEqual(map.y,y);
    input.onSelect(); Test.assertEqual(view.control,2);
    map.x=OfflineMap.MAP_WIDTH/2.0d; var x=map.x;
    input.onPreviousPage(); Test.assert(map.x < x);
    input.onNextPage(); Test.assertEqual(map.x,x);
    input.onSelect(); Test.assertEqual(view.control,0);
    Test.assert(!map.follow && !map.recording && map.gpsX == null);
    view.renderer.release();
    return true;
}
