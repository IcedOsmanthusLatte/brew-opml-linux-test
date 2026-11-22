# Linux OPML round trip

This one-off GitHub Actions workflow compares subscription portability in RSS Guard and Newsboat using two fixtures containing public news feeds. It runs on the `linux-opml-roundtrip` branch.

The control has three feeds in two folders. The stress fixture introduces a nested folder and lists the same WordPress News feed in two locations. An empty profile is used for each import. Newsboat exports both its default format and its tag-enabled OPML 2.0 format. RSS Guard imports and exports each of those files separately with online metadata fetching disabled. The stress fixture also has a separately labeled repair check that fills blank titles using the original input, leaving URLs and grouping attributes untouched. The stress case is repeated, then the workflow compares the feed URLs, folder paths, and category attributes.

The workflow records the application versions, raw outputs and screenshots as the `linux-opml-evidence` artifact. This experiment measures the subscription list. It does not measure article fetching or the migration of reading history.

The inputs are based on the public Brew [normal](https://getbrew.app/collections/downloads/opml-migration-normal.opml) and [duplicate](https://getbrew.app/collections/downloads/opml-migration-duplicate.opml) fixtures. The stress variant adds a Research folder without changing the URLs.
