import Toybox.Test;
import Toybox.System;
import Toybox.Math;

// Profile this with the datafield manifest. SDK unit tests have an enlarged
// heap, so these measurements do not replace a production-mode/device run.
// Geometry is created at runtime instead of hiding it in a test literal.
(:test)
function maximumNavigationGeometryScansAndDrawsRealTiles(logger) {
    var map=freshCamera();
    map.recording=true; map.running=true;
    map.navigation=[];
    for(var i=0;i<2047;i++) {
        map.navigation.add(OfflineMap.START_X+80.0d+(i%64)*0.5d);
        map.navigation.add(OfflineMap.START_Y+50.0d+Math.floor(i/64.0d)*0.5d);
    }
    // Only the final segment reaches the fix. A truncated scan would choose
    // a wider view, so the first close-zoom assertion checks the array tail.
    map.navigation.add(OfflineMap.START_X.toDouble());
    map.navigation.add(OfflineMap.START_Y.toDouble());
    Test.assertEqual(map.navigation.size(),4096);
    var allocated=System.getSystemStats();
    logger.debug("navigation-load points=2048 entries=4096 used="+
        allocated.usedMemory+" free="+allocated.freeMemory+" total="+allocated.totalMemory);

    var renderer=new TrailRenderer(map);
    var speeds=[1.4,4.5,6.5];
    for(var pass=0;pass<3;pass++) {
        // Exercise close 2x, normal detail and wider tiles without spending
        // twelve extra scans waiting for the separate hysteresis behaviour.
        map.navigationReady=false;
        var px=OfflineMap.START_X+pass*0.25d;
        var py=OfflineMap.START_Y-pass*0.25d;
        var before=System.getSystemStats();
        var began=System.getTimer();
        map.updateMotion(px,py,0,speeds[pass],4);
        var updateMs=System.getTimer()-began;
        var afterUpdate=System.getSystemStats();
        Test.assert(map.isTracking());
        Test.assertEqual(map.x,px); Test.assertEqual(map.y,py);
        Test.assertEqual(map.zoom,trailMax(OfflineMap.MIN_ZOOM,map.maxZoom()-pass));
        Test.assertEqual(map.navigation.size(),4096);

        var dc=new PreviewTestCanvas();
        var drawBegan=System.getTimer();
        renderer.draw(dc,null);
        var drawMs=System.getTimer()-drawBegan;
        var afterDraw=System.getSystemStats();
        Test.assert(dc.count>0 && dc.count<=9);
        Test.assertEqual(dc.polygons.size(),3);
        dc.assertFullCoverage();
        logger.debug("navigation-load pass="+pass+" zoom="+map.zoom+
            " updateMs="+updateMs+" drawMs="+drawMs+
            " usedBefore="+before.usedMemory+
            " usedAfterUpdate="+afterUpdate.usedMemory+
            " usedAfterDraw="+afterDraw.usedMemory+
            " freeAfterDraw="+afterDraw.freeMemory+" tiles="+dc.count);
        // Keep only the renderer's deliberate single-tile cache between
        // passes, not the test canvas's recorded draw calls and polygons.
        dc=null;
    }
    renderer.release();
    map.navigation=null;
    return true;
}
