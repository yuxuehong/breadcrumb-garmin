import Cocoa
import WebKit
import UniformTypeIdentifiers

final class TrailDesktop: NSObject, NSApplicationDelegate, WKUIDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var process: Process?
    var received = Data()
    var launched = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "退出 Breadcrumb", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu; menu.addItem(appItem)
        let editItem = NSMenuItem(title: "编辑", action: nil, keyEquivalent: "")
        let edit = NSMenu(title: "编辑")
        for (title, action, key) in [("拷贝", "copy:", "c"), ("粘贴", "paste:", "v"), ("全选", "selectAll:", "a")] {
            edit.addItem(withTitle: title, action: Selector(action), keyEquivalent: key)
        }
        editItem.submenu = edit; menu.addItem(editItem); NSApp.mainMenu = menu
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1180, height: 850),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Breadcrumb · 离线赛道地图"
        window.minSize = NSSize(width: 850, height: 650)
        window.center()
        web = WKWebView(frame: .zero)
        web.uiDelegate = self; web.navigationDelegate = self
        window.contentView = web
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        guard let resource = Bundle.main.resourceURL,
              let root = try? String(contentsOf: resource.appendingPathComponent("project-root.txt"), encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines) else {
            showError("找不到项目目录，请重新运行 Mac 初始化程序。"); return
        }
        let task = Process(); process = task
        task.executableURL = URL(fileURLWithPath: root + "/.venv/bin/python")
        task.arguments = [root + "/mac/server.py"]
        task.currentDirectoryURL = URL(fileURLWithPath: root)
        let pipe = Pipe(); task.standardOutput = pipe
        task.standardError = FileHandle.nullDevice
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            DispatchQueue.main.async {
                guard let self = self, !self.launched else { return }
                self.received.append(data)
                if let text = String(data: self.received, encoding: .utf8), let line = text.split(separator: "\n").first,
                   line.hasPrefix("http://127.0.0.1:"), let url = URL(string: String(line)) {
                    self.launched = true
                    self.web.load(URLRequest(url: url))
                }
            }
        }
        do { try task.run() } catch { showError("无法启动本地地图服务，请先运行 Mac 初始化程序。") }
    }

    func showError(_ message: String) {
        let alert = NSAlert(); alert.messageText = message; alert.runModal()
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ notification: Notification) {
        if process?.isRunning == true { process?.interrupt() }
    }
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel(); panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        if let type = UTType(filenameExtension: "gpx") { panel.allowedContentTypes = [type] }
        panel.beginSheetModal(for: window) { response in completionHandler(response == .OK ? panel.urls : nil) }
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let url = navigationAction.request.url, url.host != "127.0.0.1", url.scheme == "https" {
            NSWorkspace.shared.open(url); decisionHandler(.cancel)
        } else { decisionHandler(.allow) }
    }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url, url.scheme == "https" { NSWorkspace.shared.open(url) }
        return nil
    }
}
let app = NSApplication.shared
let delegate = TrailDesktop()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
