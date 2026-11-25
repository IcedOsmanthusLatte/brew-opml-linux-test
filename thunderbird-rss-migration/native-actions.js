const params = Array.from(arguments);
const done = params.pop();
const [action, input] = params;

(async () => {
  const { MailServices } = ChromeUtils.importESModule("resource:///modules/MailServices.sys.mjs");
  const { FeedUtils } = ChromeUtils.importESModule("resource:///modules/FeedUtils.sys.mjs");
  const win = Services.wm.getMostRecentWindow("mail:3pane");
  const sleep = ms => new Promise(resolve => win.setTimeout(resolve, ms));
  const rssAccounts = () => Array.from(MailServices.accounts.accounts)
    .filter(account => account.incomingServer.type === "rss");
  const root = () => {
    const accounts = rssAccounts();
    if (accounts.length !== 1) {
      throw new Error("Expected exactly one isolated RSS account, found " + accounts.length);
    }
    return accounts[0].incomingServer.rootFolder;
  };
  function findFile(path) {
    const file = Components.classes["@mozilla.org/file/local;1"]
      .createInstance(Components.interfaces.nsIFile);
    file.initWithPath(path);
    return file;
  }
  function walk(folder, visit, path = []) {
    visit(folder, path);
    for (const child of folder.subFolders) {
      if (child.flags & Components.interfaces.nsMsgFolderFlags.Trash) {
        continue;
      }
      walk(child, visit, [...path, child.prettyName]);
    }
  }
  function snapshot() {
    const accounts = rssAccounts();
    if (!accounts.length) {
      return { account: null, folders: [], messages: [] };
    }
    const folders = [];
    const messages = [];
    walk(root(), (folder, path) => {
      if (folder.isServer) {
        return;
      }
      folders.push({ path, urls: FeedUtils.getFeedUrlsInFolder(folder) || [] });
      for (const hdr of folder.messages) {
        messages.push({
          path,
          subject: hdr.mime2DecodedSubject,
          message_id: hdr.messageId,
          read: hdr.isRead,
          starred: hdr.isFlagged,
        });
      }
    });
    return {account: root().prettyName, folders, messages};
  }
  async function withSubscriptions(callback) {
    const dlg = win.openDialog("chrome://messenger-newsblog/content/feed-subscriptions.xhtml",
      "", "chrome,dialog=no,resizable", {folder: root()});
    try {
      for (let n = 0; n < 60; n++) {
        if (dlg.FeedSubscriptions?.mView?.currentItem?.folder?.isServer) {
          return await callback(dlg.FeedSubscriptions);
        }
        await sleep(125);
      }
      throw new Error("Feed Subscriptions dialog did not select account root");
    } finally {
      dlg.close();
    }
  }

  switch (action) {
    case "ready":
      return {version: Services.appinfo.version, build_id: Services.appinfo.appBuildID,
        account_count: rssAccounts().length, window: win?.document.documentURI || null};
    case "create_account":
      return FeedUtils.createRssAccount("Brew Test Feeds").incomingServer.rootFolder.prettyName;
    case "snapshot":
      return snapshot();
    case "import_opml":
      return withSubscriptions(async ui => {
        const file = findFile(input);
        // Supply the selected file; keep the application's UI import logic unchanged.
        const chooser = ui.opmlPickOpenFile;
        ui.opmlPickOpenFile = async () => [file, Services.io.newFileURI(file).spec];
        try {
          await ui.importOPML();
          for (let n = 0; n < 120; n++) {
            if (ui.mActionMode !== ui.kImportingOPML) {
              return snapshot();
            }
            await sleep(125);
          }
          throw new Error("Built-in OPML import did not finish");
        } finally {
          ui.opmlPickOpenFile = chooser;
        }
      });
    case "export_opml":
      return withSubscriptions(async ui => {
        const chooser = ui.opmlPickSaveAsFile;
        ui.opmlPickSaveAsFile = async () => findFile(input);
        try {
          await ui.exportOPML({ctrlKey: false});
          return true;
        } finally {
          ui.opmlPickSaveAsFile = chooser;
        }
      });
    case "export_zip": {
      const { ProfileExporter } =
        ChromeUtils.importESModule("resource:///modules/ProfileExporter.sys.mjs");
      await new ProfileExporter().startExport(findFile(input));
      return true;
    }
    case "import_zip": {
      const { ThunderbirdProfileImporter } =
        ChromeUtils.importESModule("resource:///modules/ThunderbirdProfileImporter.sys.mjs");
      const importer = new ThunderbirdProfileImporter();
      const source = findFile(input);
      if (!importer.validateSource(source)) {
        throw new Error("Built-in ZIP validation failed");
      }
      const extraction = await importer.extractZipFile(source, () => {});
      try {
        return await importer.startImport(extraction,
          {accounts: true, mailMessages: true, addressBooks: false, calendars: false});
      } finally {
        extraction.remove(true);
      }
    }
    case "refresh":
      await FeedUtils.downloadFeed(root(), null, false);
      return true;
    case "mark": {
      const subjects = [];
      walk(root(), folder => {
        if (folder.isServer) {
          return;
        }
        for (const hdr of folder.messages) {
          if (input.includes(hdr.mime2DecodedSubject)) {
            folder.markMessagesRead([hdr], true);
            folder.markMessagesFlagged([hdr], true);
            subjects.push(hdr.mime2DecodedSubject);
          }
        }
      });
      return subjects.sort();
    }
    case "visible_folder": {
      const mail = win.document.getElementById("tabmail");
      mail.switchToTab(0);
      const pane = mail.currentAbout3Pane;
      let target = null;
      walk(root(), folder => {
        if (!folder.isServer && Array.from(folder.messages)
          .some(hdr => hdr.mime2DecodedSubject === input)) {
          target = folder;
        }
      });
      if (!target || !pane) {
        return false;
      }
      pane.displayFolder(target.URI);
      await sleep(350);
      return true;
    }
    case "shutdown":
      win.setTimeout(() => Services.startup.quit(Components.interfaces.nsIAppStartup.eAttemptQuit), 120);
      return true;
    default:
      throw new Error("Unrecognized test action");
  }
})().then(value => done({value}), error => done({error: String(error), stack: error.stack}));
