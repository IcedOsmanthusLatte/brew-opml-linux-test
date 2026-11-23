import AppKit
import Foundation
import XCTest

private struct Plan: Decodable {
    let app: String
    let bundle_path: String
    let fixture_path: String
    let export_dir: String
    let titles: [String]

    static func read() throws -> Self {
        let config = URL(fileURLWithPath: "/tmp/brew-opml-runtime/config.json")
        return try JSONDecoder().decode(Self.self, from: Data(contentsOf: config))
    }
}

final class NativeOPMLTests: XCTestCase {
    @MainActor
    func testNativeImportRestartAndExport() throws {
        continueAfterFailure = false
        let plan = try Plan.read()
        let app = XCUIApplication(url: URL(fileURLWithPath: plan.bundle_path))
        app.launch()
        defer { saveTree(of: app, name: "final") }
        XCTAssertTrue(app.windows.firstMatch.waitForExistence(timeout: 25))
        dismissFirstRunIfPresent(in: app)
        expandSidebar(in: app)
        saveTree(of: app, name: "before-import")
        try nativeMenu(app, section: "File", prefixOptions: ["Import Subscriptions", "Import OPML"])
        try chooseInput(in: app, path: plan.fixture_path)
        finishImportIfPresent(in: app)
        expandSidebar(in: app)
        saveTree(of: app, name: "imported")
        saveWindow(of: app, name: "imported")
        assertFixtureRows(in: app, titles: plan.titles)

        app.terminate()
        app.launch()
        XCTAssertTrue(app.windows.firstMatch.waitForExistence(timeout: 15))
        expandSidebar(in: app)
        saveTree(of: app, name: "restarted")
        saveWindow(of: app, name: "restarted")
        assertFixtureRows(in: app, titles: plan.titles)

        try nativeMenu(app, section: "File", prefixOptions: ["Export Subscriptions", "Export OPML"])
        try chooseOutput(in: app, directory: plan.export_dir)
        let expected = URL(fileURLWithPath: plan.export_dir).appendingPathComponent("native-export.opml")
        for _ in 0..<20 {
            if FileManager.default.fileExists(atPath: expected.path) { return }
            Thread.sleep(forTimeInterval: 0.4)
        }
        XCTFail("Native export was not observed")
    }

    @MainActor
    private func nativeMenu(_ app: XCUIApplication, section: String, prefixOptions: [String]) throws {
        let fileMenu = app.menuBars.firstMatch.menuBarItems[section]
        XCTAssertTrue(fileMenu.waitForExistence(timeout: 12), "No native File menu")
        fileMenu.click()
        let queries = [fileMenu.descendants(matching: .menuItem), app.descendants(matching: .menuItem)]
        var item: XCUIElement?
        for items in queries {
            for prefix in prefixOptions {
                let found = items.matching(NSPredicate(format: "label BEGINSWITH[c] %@", prefix)).firstMatch
                if found.exists && found.isEnabled {
                    item = found
                    break
                }
            }
            if item != nil { break }
        }
        let labels = fileMenu.descendants(matching: .menuItem).allElementsBoundByIndex.map(\.label)
        let op = prefixOptions.first?.lowercased().hasPrefix("import") == true ? "import" : "export"
        print("BREW_MENU_\(op): \(labels)")
        attachText(labels.joined(separator: "\n"), name: op + "-menu")
        try XCTUnwrap(item, "Native item missing or disabled: \(prefixOptions). Saw: \(labels)").click()
    }

    @MainActor
    private func paste(_ value: String, into element: XCUIElement) {
        let board = NSPasteboard.general
        board.clearContents()
        board.setString(value, forType: .string)
        element.typeKey("v", modifierFlags: [.command])
    }

    @MainActor
    private func goToPath(_ path: String, in app: XCUIApplication) {
        app.typeKey("g", modifierFlags: [.command, .shift])
        Thread.sleep(forTimeInterval: 0.6)
        app.typeKey("a", modifierFlags: [.command])
        paste(path, into: app)
        Thread.sleep(forTimeInterval: 0.9)
        app.typeKey(XCUIKeyboardKey.return, modifierFlags: [])
        Thread.sleep(forTimeInterval: 0.8)
    }

    @MainActor
    private func chooseInput(in app: XCUIApplication, path: String) throws {
        let button = app.buttons["Open"].firstMatch
        if !button.waitForExistence(timeout: 7) {
            let chooser = app.buttons["Choose File"].firstMatch
            if chooser.exists && chooser.isEnabled { chooser.click() }
        }
        XCTAssertTrue(button.waitForExistence(timeout: 10), "Native file open dialog did not appear")
        goToPath(path, in: app)
        if button.exists && button.isEnabled { button.click() }
    }

    @MainActor
    private func chooseOutput(in app: XCUIApplication, directory: String) throws {
        let save = app.buttons.matching(NSPredicate(format: "label ==[c] %@ OR label ==[c] %@",
                                                    "Save", "Export OPML")).firstMatch
        XCTAssertTrue(save.waitForExistence(timeout: 8), "Native save dialog did not appear")
        let group = app.checkBoxes.matching(NSPredicate(format: "label CONTAINS[c] %@", "group")).firstMatch
        if group.exists, let value = group.value as? String, value == "0" { group.click() }
        if group.exists, let value = group.value as? NSNumber, value.intValue == 0 { group.click() }
        goToPath(directory, in: app)
        let named = app.sheets.firstMatch.textFields.firstMatch
        if named.exists && named.isHittable {
            named.click()
            named.typeKey("a", modifierFlags: [.command])
            paste("native-export.opml", into: named)
        } else {
            app.typeKey("a", modifierFlags: [.command])
            paste("native-export.opml", into: app)
        }
        XCTAssertTrue(save.exists && save.isEnabled)
        save.click()
    }

    @MainActor
    private func dismissFirstRunIfPresent(in app: XCUIApplication) {
        for label in ["Not Now", "Continue", "OK"] {
            let dialogButton = app.sheets.firstMatch.buttons[label]
            if dialogButton.exists && dialogButton.isEnabled {
                dialogButton.click()
                Thread.sleep(forTimeInterval: 0.3)
            }
        }
    }

    @MainActor
    private func finishImportIfPresent(in app: XCUIApplication) {
        Thread.sleep(forTimeInterval: 2)
        for label in ["OK", "Done"] {
            let candidates = [app.sheets.firstMatch.buttons[label], app.dialogs.firstMatch.buttons[label]]
            for button in candidates where button.exists && button.isEnabled {
                button.click()
                return
            }
        }
    }

    @MainActor
    private func expandSidebar(in app: XCUIApplication) {
        for _ in 0..<4 {
            let triangles = app.outlines.firstMatch.descendants(matching: .disclosureTriangle).allElementsBoundByIndex
            var expanded = false
            for triangle in triangles.prefix(20) where triangle.exists && triangle.isHittable {
                if let value = triangle.value as? NSNumber, value.intValue == 0 {
                    triangle.click()
                    expanded = true
                } else if let value = triangle.value as? String, value == "0" {
                    triangle.click()
                    expanded = true
                }
            }
            if !expanded { break }
        }
    }

    @MainActor
    private func assertFixtureRows(in app: XCUIApplication, titles: [String]) {
        let outline = app.outlines.firstMatch
        XCTAssertTrue(outline.waitForExistence(timeout: 10), "No native subscriptions sidebar")
        let tree = outline.debugDescription.lowercased()
        let names = ["bbc", "nasa", "wordpress"]
        XCTAssertEqual(titles.count, names.count)
        for name in names {
            XCTAssertTrue(tree.contains(name), "Sidebar has no imported entry for \(name)")
        }
    }

    @MainActor
    private func saveTree(of app: XCUIApplication, name: String) {
        let outline = app.outlines.firstMatch
        let value = outline.exists ? outline.debugDescription : app.debugDescription
        attachText(String(value.prefix(36_000)), name: name + "-tree")
    }

    @MainActor
    private func saveWindow(of app: XCUIApplication, name: String) {
        let element = app.windows.firstMatch
        guard element.exists else { return }
        let artifact = XCTAttachment(screenshot: element.screenshot())
        artifact.name = "brew-" + name + "-window"
        artifact.lifetime = .keepAlways
        add(artifact)
    }

    private func attachText(_ text: String, name: String) {
        let artifact = XCTAttachment(string: text)
        artifact.name = "brew-" + name
        artifact.lifetime = .keepAlways
        add(artifact)
    }
}
