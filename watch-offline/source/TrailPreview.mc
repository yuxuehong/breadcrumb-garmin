import Toybox.WatchUi;
import Toybox.Graphics;

class TrailPreview extends WatchUi.View {
    var map;
    var renderer;
    var control = 0;
    var firstFit = false;
    var width = 260;
    var height = 260;
    function initialize(state,painter) {
        View.initialize(); map = state; renderer = painter;
        firstFit = map.follow; map.beginPreview();
    }
    function onUpdate(dc) {
        width = dc.getWidth(); height = dc.getHeight();
        if (firstFit) { map.fit(width,height); firstFit = false; }
        renderer.draw(dc,control);
    }
    function onHide() { renderer.release(); map.save(); }
    function adjust(direction) {
        if (control == 0) { map.changeZoom(direction); }
        else if (control == 1) { map.pan(0,-direction); }
        else { map.pan(-direction,0); }
        WatchUi.requestUpdate();
    }
    function setControl(mode) {
        control = mode; map.beginPreview(); WatchUi.requestUpdate();
    }
}

class TrailPreviewDelegate extends WatchUi.BehaviorDelegate {
    var view;
    function initialize(preview) { BehaviorDelegate.initialize(); view = preview; }
    function onPreviousPage() { view.adjust(1); return true; }
    function onNextPage() { view.adjust(-1); return true; }
    function onSelect() {
        view.setControl((view.control+1)%3); return true;
    }
    function onBack() { view.map.save(); WatchUi.popView(WatchUi.SLIDE_RIGHT); return true; }
    function onMenu() {
        var menu = new WatchUi.Menu2({:title=>trailText(Rez.Strings.MapMenu)});
        menu.addItem(new WatchUi.MenuItem(trailText(Rez.Strings.FullRoute),null,:overview,{}));
        menu.addItem(new WatchUi.MenuItem(trailText(Rez.Strings.ToStart),null,:start,{}));
        menu.addItem(new WatchUi.MenuItem(trailText(Rez.Strings.ZoomMode),null,:zoom,{}));
        menu.addItem(new WatchUi.MenuItem(trailText(Rez.Strings.NorthSouthMode),null,:northsouth,{}));
        menu.addItem(new WatchUi.MenuItem(trailText(Rez.Strings.WestEastMode),null,:westeast,{}));
        if (!view.map.standalone) {
            menu.addItem(new WatchUi.MenuItem(trailText(Rez.Strings.FollowAction),null,:follow,{}));
        }
        menu.addItem(new WatchUi.MenuItem(trailText(Rez.Strings.MapAbout),null,:about,{}));
        WatchUi.pushView(menu,new TrailMenuDelegate(view),WatchUi.SLIDE_UP); return true;
    }
}

class TrailMenuDelegate extends WatchUi.Menu2InputDelegate {
    var view;
    function initialize(preview) { Menu2InputDelegate.initialize(); view = preview; }
    function onSelect(item) {
        var id = item.getId();
        if (id == :about) {
            var about = new WatchUi.Menu2({:title=>trailText(Rez.Strings.MapAbout)});
            about.addItem(new WatchUi.MenuItem(trailText(Rez.Strings.AttributionData),trailText(Rez.Strings.AttributionOsmUrl),:osm,{}));
            about.addItem(new WatchUi.MenuItem(trailText(Rez.Strings.AttributionTerrain),null,:srtm,{}));
            about.addItem(new WatchUi.MenuItem(trailText(Rez.Strings.AttributionStyle),trailText(Rez.Strings.AttributionMapUrl),:otm,{}));
            about.addItem(new WatchUi.MenuItem(trailText(Rez.Strings.AttributionLicense),trailText(Rez.Strings.AttributionLicenseUrl),:license,{}));
            WatchUi.pushView(about,new TrailAboutDelegate(),WatchUi.SLIDE_UP); return;
        }
        if (id == :overview) { view.map.beginPreview(); view.map.fit(view.width,view.height); view.control=0; }
        else if (id == :start) { view.map.startPoint(); view.control=0; }
        else if (id == :follow) { view.map.followLocation(); view.control=0; }
        else if (id == :zoom) { view.setControl(0); }
        else if (id == :northsouth) { view.setControl(1); }
        else if (id == :westeast) { view.setControl(2); }
        view.map.save();
        WatchUi.popView(WatchUi.SLIDE_DOWN); WatchUi.requestUpdate();
    }
}

// Selecting a provider shows its complete address with wrapping. Menu2
// subtitles alone can truncate a long license URL on a small round display.
class TrailAboutDelegate extends WatchUi.Menu2InputDelegate {
    function initialize() { Menu2InputDelegate.initialize(); }
    function onSelect(item) {
        var id=item.getId();
        var lines = id==:osm ? [Rez.Strings.AttributionData,Rez.Strings.AttributionOsmUrl]
            : id==:otm ? [Rez.Strings.AttributionStyle,Rez.Strings.AttributionMapUrl]
            : id==:license ? [Rez.Strings.AttributionLicense,Rez.Strings.AttributionLicenseUrl]
            : [Rez.Strings.AttributionTerrain];
        WatchUi.pushView(new TrailCreditView(lines),new TrailCreditDelegate(),WatchUi.SLIDE_UP);
    }
}

class TrailCreditView extends WatchUi.View {
    var credits;
    function initialize(lines) { View.initialize(); credits=lines; }
    function onUpdate(dc) { trailDrawCredits(dc,credits); }
}

class TrailCreditDelegate extends WatchUi.BehaviorDelegate {
    function initialize() { BehaviorDelegate.initialize(); }
    function onBack() { WatchUi.popView(WatchUi.SLIDE_RIGHT); return true; }
    function onSelect() { return onBack(); }
}

function trailDrawCredits(dc,credits) {
    var w=dc.getWidth(),h=dc.getHeight();
    var font=Graphics.FONT_XTINY;
    var lineHeight=dc.getFontHeight(font)+2;
    var maxWidth=trailMin(w,h)*0.68;
    var lines=[];
    for(var i=0;i<credits.size();i++) {
        var text=trailText(credits[i]);
        var line="";
        for(var c=0;c<text.length();c++) {
            var next=text.substring(c,c+1);
            if(line.length()>0 && dc.getTextWidthInPixels(line+next,font)>maxWidth) {
                var lastSpace=-1;
                for(var p=0;p<line.length();p++) {
                    if(line.substring(p,p+1).equals(" ")) { lastSpace=p; }
                }
                if(lastSpace>0) {
                    lines.add(line.substring(0,lastSpace)); line=line.substring(lastSpace+1,line.length());
                } else { lines.add(line); line=""; }
            }
            line+=next;
        }
        if(line.length()>0) { lines.add(line); }
    }
    dc.setColor(Graphics.COLOR_BLACK,Graphics.COLOR_WHITE); dc.clear();
    var y=(h-lines.size()*lineHeight)/2;
    for(var row=0;row<lines.size();row++) {
        dc.drawText(w/2,y+row*lineHeight,font,lines[row],Graphics.TEXT_JUSTIFY_CENTER);
    }
}
