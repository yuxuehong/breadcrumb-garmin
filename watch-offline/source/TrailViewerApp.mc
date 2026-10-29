import Toybox.Application;

// Pre-race browsing is an independent app, so the watch's activity settings
// menu and the current GPS location cannot hide or interrupt its controls.
// No GPS or activity recording is started by this app.
class TrailViewerApp extends Application.AppBase {
    var map;
    var renderer;
    function initialize() {
        AppBase.initialize();
        map = new TrailMapState();
        map.standalone = true;
        renderer = new TrailRenderer(map);
    }
    function getInitialView() {
        var view = new TrailPreview(map,renderer);
        return [view,new TrailPreviewDelegate(view)];
    }
    function onStop(state) { map.save(); }
}
