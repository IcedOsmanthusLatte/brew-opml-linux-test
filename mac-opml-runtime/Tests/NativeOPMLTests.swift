import AppKit
import Foundation
import Vision
import XCTest

private struct Plan: Decodable {
    let app: String
    let mode: String
    let bundle_path: String
    let fixture_path: String
    let export_dir: String
    let titles: [String]
    let selection_mode: String?

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
        defer { attachText(String(app.debugDescription.prefix(42_000)), name: "final-tree") }
        XCTAssertTrue(app.windows.firstMatch.waitForExistence(timeout: 25))
        app.activate()
        dismissFirstRunIfPresent(in: app)
        saveWindow(of: app, name: "default-starters")
        try removeDefaultFeeds(from: app, kind: plan.app)
        expandSidebar(in: app)
        if let selection = plan.selection_mode {
            try arrangeViennaDestination(in: app, plan: plan, mode: selection)
        } else {
            saveTree(of: app, name: "before-import")
            saveWindow(of: app, name: "before-import")
        }
        try nativeMenu(app, section: "File", prefixOptions: ["Import Subscriptions", "Import OPML"])
        try chooseInput(in: app, path: plan.fixture_path)
        finishImportIfPresent(in: app)
        try revealFixtureFolders(in: app, mode: plan.mode)
        saveTree(of: app, name: "imported")
        if plan.selection_mode == nil { saveWindow(of: app, name: "imported") }
        try assertFixtureRows(in: app, titles: plan.titles, stage: "imported")

        app.terminate()
        app.launch()
        XCTAssertTrue(app.windows.firstMatch.waitForExistence(timeout: 15))
        app.activate()
        dismissFirstRunIfPresent(in: app)
        try revealFixtureFolders(in: app, mode: plan.mode)
        saveTree(of: app, name: "restarted")
        if plan.selection_mode == nil { saveWindow(of: app, name: "restarted") }
        try assertFixtureRows(in: app, titles: plan.titles, stage: "restarted")

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
    private func arrangeViennaDestination(in app: XCUIApplication, plan: Plan, mode: String) throws {
        XCTAssertEqual(plan.app, "vienna")
        XCTAssertTrue(["selected", "unselected"].contains(mode))
        let target = "Brew Import Target"
        let seed = URL(fileURLWithPath: plan.fixture_path).deletingLastPathComponent().appendingPathComponent("target-only.opml")
        try nativeMenu(app, section: "File", prefixOptions: ["Import Subscriptions", "Import OPML"])
        try chooseInput(in: app, path: seed.path)
        finishImportIfPresent(in: app)
        let label = try XCTUnwrap(try sidebarText(in: app).first { textKey($0.0) == textKey(target) },
                                  "Expected existing empty destination after the setup import")
        rowCoordinate(in: app, box: label.1).click()
        let outline = app.outlines.firstMatch
        let selected = try readSelectionBands(in: app, label: label.1)
        XCTAssertTrue(selected.target, "Could not see active selection on the target row")
        XCTAssertEqual(selected.count, 1, "Target must be the only highlighted row")
        if mode == "unselected" {
            XCUIElement.perform(withKeyModifiers: [.command]) {
                rowCoordinate(in: app, box: label.1).click()
            }
            if try readSelectionBands(in: app, label: label.1).count != 0 {
                outline.coordinate(withNormalizedOffset: CGVector(dx: 0.75, dy: 0.85)).click()
            }
        }
        let final = try readSelectionBands(in: app, label: label.1)
        let receipt = "condition=\(mode)\ntarget=\(target)\ntarget_row_selected=\(final.target)\nselected_row_count=\(final.count)\nselection_evidence=window raster immediately before import menu"
        print("BREW_VIENNA_SELECTION: \(receipt.replacingOccurrences(of: "\n", with: " "))")
        attachText(receipt, name: "destination-selection")
        attachText("target OCR box: \(label.1)\nselected measurements: \(selected)\nfinal measurements: \(final)", name: "selection-measurement")
        saveTree(of: app, name: "before-import")
        saveWindow(of: app, name: "before-import")
        XCTAssertEqual(final.target, mode == "selected", "Wrong destination row state")
        XCTAssertEqual(final.count, mode == "selected" ? 1 : 0, "Wrong selected row count")
    }

    @MainActor
    private func readSelectionBands(in app: XCUIApplication, label: CGRect) throws -> (target: Bool, count: Int, starts: [Int]) {
        let data = mainWindow(in: app).screenshot().pngRepresentation
        let bitmap = try XCTUnwrap(NSBitmapImageRep(data: data))
        let width = bitmap.pixelsWide
        let height = bitmap.pixelsHigh
        // Probe the empty portion of the sidebar after labels and before the split view divider.
        let x = Int(CGFloat(width) * 0.24)
        let colors = (0..<height).map { y -> Bool in
            guard let color = bitmap.colorAt(x: x, y: y)?.usingColorSpace(.sRGB) else { return false }
            return color.blueComponent - color.redComponent > 0.38 && color.blueComponent - color.greenComponent > 0.21
        }
        var bands: [Range<Int>] = []
        var start: Int?
        for y in 0...height {
            if y < height, colors[y] {
                if start == nil { start = y }
            } else if let first = start {
                if y - first >= 8 { bands.append(first..<y) }
                start = nil
            }
        }
        let y = Int(CGFloat(height) * (1 - label.midY))
        let atTarget = bands.contains { $0.contains(y) || $0.contains(height - 1 - y) }
        return (atTarget, bands.count, bands.map(\.lowerBound))
    }

    @MainActor
    private func nativeMenu(_ app: XCUIApplication, section: String, prefixOptions: [String], operation: String? = nil) throws {
        let fileMenu = app.menuBars.firstMatch.menuBarItems[section]
        XCTAssertTrue(fileMenu.waitForExistence(timeout: 12), "No native File menu")
        fileMenu.click()
        let items = fileMenu.descendants(matching: .menuItem).allElementsBoundByIndex
        let names = items.map { item in
            [item.title, item.label, item.identifier].filter { !$0.isEmpty }
        }
        let op = operation ?? (prefixOptions.first?.lowercased().hasPrefix("import") == true ? "import" : "export")
        print("BREW_MENU_\(op): \(names)")
        attachText(String(fileMenu.debugDescription.prefix(25_000)), name: op + "-menu")
        let options = prefixOptions + (op == "import" ? ["Import Feeds"] : ["Export Feeds"])
        let selected = zip(items, names).first { item, attributes in
            item.isEnabled && attributes.contains { name in
                options.contains { name.lowercased().hasPrefix($0.lowercased()) }
            }
        }?.0
        try XCTUnwrap(selected, "Native item missing or disabled: \(options). Saw: \(names)").click()
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
        let button = namedButton(app, options: ["Open", "Import", "Import OPML"])
        if !button.waitForExistence(timeout: 7) {
            let chooser = app.buttons["Choose File"].firstMatch
            if chooser.exists && chooser.isEnabled { chooser.click() }
        }
        XCTAssertTrue(button.waitForExistence(timeout: 10), "Native file open dialog did not appear")
        goToPath(path, in: app)
        attachText(String(app.debugDescription.prefix(38_000)), name: "file-chooser")
        if button.exists && button.isEnabled {
            app.typeKey(XCUIKeyboardKey.return, modifierFlags: [])
        }
    }

    @MainActor
    private func chooseOutput(in app: XCUIApplication, directory: String) throws {
        let save = namedButton(app, options: ["Save", "Export", "Export OPML"])
        XCTAssertTrue(save.waitForExistence(timeout: 8), "Native save dialog did not appear")
        let group = app.windows.firstMatch.checkBoxes.matching(
            NSPredicate(format: "label CONTAINS[c] %@ OR title CONTAINS[c] %@", "group", "group")
        ).firstMatch
        if group.exists, let value = group.value as? String, value == "0" { group.click() }
        if group.exists, let value = group.value as? NSNumber, value.intValue == 0 { group.click() }
        let named = app.sheets.firstMatch.textFields.firstMatch
        if named.exists && named.isHittable {
            named.click()
            named.typeKey("a", modifierFlags: [.command])
            paste("native-export.opml", into: named)
        } else {
            app.typeKey("a", modifierFlags: [.command])
            paste("native-export.opml", into: app)
        }
        goToPath(directory, in: app)
        attachText(String(app.debugDescription.prefix(38_000)), name: "save-chooser")
        XCTAssertTrue(save.exists && save.isEnabled)
        app.typeKey(XCUIKeyboardKey.return, modifierFlags: [])
    }

    @MainActor
    private func dismissFirstRunIfPresent(in app: XCUIApplication) {
        let options = ["Don't Check", "Don’t Check", "Check Manually", "Not Now", "Maybe Later", "Skip", "Continue", "OK"]
        for _ in 0..<3 {
            let scoped = [app.sheets.firstMatch, app.dialogs.firstMatch] + app.windows.allElementsBoundByIndex
            for area in scoped where area.exists {
                let button = namedButton(area, options: options)
                if button.exists && button.isEnabled && button.isHittable { button.click() }
            }
            Thread.sleep(forTimeInterval: 0.25)
        }
    }

    @MainActor
    private func finishImportIfPresent(in app: XCUIApplication) {
        Thread.sleep(forTimeInterval: 2)
        for label in ["OK", "Done"] {
            let candidates = [namedButton(app.sheets.firstMatch, options: [label]),
                              namedButton(app.dialogs.firstMatch, options: [label])]
            for button in candidates where button.exists && button.isEnabled {
                button.click()
                return
            }
        }
    }

    @MainActor
    private func namedButton(_ element: XCUIElement, options: [String]) -> XCUIElement {
        element.buttons.matching(NSPredicate(format: "label IN %@ OR title IN %@", options, options)).firstMatch
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
    private func sidebarText(in app: XCUIApplication) throws -> [(String, CGRect)] {
        let window = mainWindow(in: app)
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = ["en-US"]
        let input = VNImageRequestHandler(data: window.screenshot().pngRepresentation)
        try input.perform([request])
        return (request.results ?? []).compactMap { row in
            guard row.boundingBox.midX < 0.32, row.boundingBox.midY < 0.93,
                  row.boundingBox.midY > 0.09, let text = row.topCandidates(1).first else { return nil }
            return (text.string, row.boundingBox)
        }.sorted { $0.1.midY > $1.1.midY }
    }

    private func textKey(_ value: String) -> String {
        value.lowercased().filter { $0.isLetter || $0.isNumber }
    }

    @MainActor
    private func rowCoordinate(in app: XCUIApplication, box: CGRect) -> XCUICoordinate {
        mainWindow(in: app).coordinate(withNormalizedOffset: CGVector(dx: box.midX, dy: 1 - box.midY))
    }

    @MainActor
    private func removeDefaultFeeds(from app: XCUIApplication, kind: String) throws {
        Thread.sleep(forTimeInterval: 1.0)
        let vienna = kind == "vienna"
        let starterNames = vienna
            ? ["It's Nice That", "Nomadic Matt", "Xkcd", "Vienna Support", "A Working Library",
               "Vienna Developer Blog", "Cool Hunting", "BBC World News", "Amusing Planet",
               "Ars Technica", "Six Colors", "Apple News", "Kottke", "Astronomy Picture of the Day"]
            : ["Craig Hockenberry", "Daring Fireball", "inessential", "Jason Kottke", "Julia Evans",
               "Manton Reece", "Michael Tsai", "NetNewsWire Blog", "Scripting News", "Six Colors"]
        let starting = try sidebarText(in: app)
        let starterKeys = starterNames.map(textKey)
        func isStarter(_ row: (String, CGRect)) -> Bool {
            let key = textKey(row.0)
            return starterKeys.contains { key.contains($0) }
        }
        let starters = starting.filter(isStarter)
        XCTAssertGreaterThanOrEqual(starters.count, starterNames.count - 2,
                                    "The standard starter subscription block could not be located")
        let top = try XCTUnwrap(starters.first)
        let bottom = try XCTUnwrap(starters.last)
        attachText(starting.map { $0.0 }.joined(separator: "\n"), name: "default-sidebar")
        rowCoordinate(in: app, box: top.1).click()
        XCUIElement.perform(withKeyModifiers: [.shift]) {
            rowCoordinate(in: app, box: bottom.1).click()
        }
        try nativeMenu(app, section: vienna ? "Folder" : "Edit", prefixOptions: ["Delete"], operation: "clear")
        Thread.sleep(forTimeInterval: 0.7)
        let options = ["Delete", "Remove", "Unsubscribe"]
        let dialogs = [app.sheets.firstMatch, app.dialogs.firstMatch]
        for dialog in dialogs where dialog.exists {
            let action = namedButton(dialog, options: options)
            if action.exists && action.isEnabled && action.isHittable {
                action.click()
                break
            }
        }
        Thread.sleep(forTimeInterval: 1.2)
        let cleared = try sidebarText(in: app)
        attachText(cleared.map { $0.0 }.joined(separator: "\n"), name: "before-import-sidebar")
        XCTAssertFalse(cleared.contains(where: isStarter),
                       "Starter subscriptions remain in the isolated profile: \(cleared.map { $0.0 })")
        XCTAssertTrue(cleared.contains { textKey($0.0).contains(vienna ? "unreadarticles" : "allunread") },
                      "Cleared account sidebar was not legible")
    }

    @MainActor
    private func revealFixtureFolders(in app: XCUIApplication, mode: String) throws {
        expandSidebar(in: app)
        let labels: [String]
        switch mode {
        case "baseline": labels = ["News", "Science and technology"]
        case "flattened": labels = ["Brew Alpha - Brew Shared", "Brew Beta - Brew Shared", "Brew Shallow"]
        case "unique": labels = ["Brew Alpha", "Brew Beta", "Brew Shared A", "Brew Shared B", "Brew Shallow"]
        default: labels = ["Brew Alpha", "Brew Beta", "Brew Shared", "Brew Shallow"]
        }
        for label in labels {
            let hits = try sidebarText(in: app).filter { textKey($0.0) == textKey(label) }
            for hit in hits {
                rowCoordinate(in: app, box: hit.1).click()
                app.typeKey(XCUIKeyboardKey.rightArrow, modifierFlags: [])
            }
        }
    }

    @MainActor
    private func assertFixtureRows(in app: XCUIApplication, titles: [String], stage: String) throws {
        let outline = app.outlines.firstMatch
        XCTAssertTrue(outline.waitForExistence(timeout: 10), "No native subscriptions sidebar")
        let rows = try sidebarText(in: app).map { $0.0 }
        attachText(rows.joined(separator: "\n"), name: stage + "-sidebar")
        let names = ["bbc", "nasa", "wordpress"]
        XCTAssertEqual(titles.count, names.count)
        for name in names {
            XCTAssertTrue(rows.contains { textKey($0).contains(name) }, "Sidebar has no imported entry for \(name)")
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
        let element = mainWindow(in: app)
        guard element.exists else { return }
        let artifact = XCTAttachment(screenshot: element.screenshot())
        artifact.name = "brew-" + name + "-window"
        artifact.lifetime = .keepAlways
        add(artifact)
    }

    @MainActor
    private func mainWindow(in app: XCUIApplication) -> XCUIElement {
        let named = app.windows.matching(identifier: "mainWindow").firstMatch
        return named.exists ? named : app.windows.firstMatch
    }

    private func attachText(_ text: String, name: String) {
        let artifact = XCTAttachment(string: text)
        artifact.name = "brew-" + name
        artifact.lifetime = .keepAlways
        add(artifact)
    }
}
