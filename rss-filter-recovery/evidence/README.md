# RSS filtering recovery in FreshRSS and Miniflux

On November 26, 2026, two independent GitHub-hosted Ubuntu 24.04 runners tested FreshRSS 1.30.4 and Miniflux 2.3.5. Each runner started with empty libraries and imported the exact same two-feed [OPML fixture](../sample.opml). All feed entries and article pages were synthetic. No third-party feed changed during either trial.

## What happened in each trial

| Situation | FreshRSS auto-mark as read | Miniflux Entry Filtering block |
| --- | --- | --- |
| Two title/body matches per feed arrived after the rule was enabled | All four matches stored as read and visible when the feed displayed read entries | None of the four matches were stored |
| We removed the rule and refreshed the feed still carrying the two matches | Both were still stored and marked read | Both appeared as new unread entries |
| We removed the rule and refreshed the feed that had dropped the two matches from its XML | Both were still stored and marked read | Neither appeared |
| A new title match was published after removing the rule | Arrived unread in both feeds | Arrived unread in both feeds |

The old title matches with unchanged feed payload remained unread in both apps. A hit present only on the linked web page stayed unread in both apps while article-page extraction was off. The matching phrase in a feed's source payload was sufficient for FreshRSS to mark the item read and Miniflux to block it in this trial.

## What to try first

- In FreshRSS, open the feed and include read articles in the listing. Removing the auto-mark-as-read rule did not change the read status of the already stored items in our test.
- In Miniflux, remove the matching rule from **Block rules** under the feed's Entry Filter Rules, then refresh so it gets the full feed again. We used the ordinary per-feed refresh with **Ignore HTTP cache** enabled, verified each full 200 response, and left article-page extraction off. The test covers entries still in the publisher's feed at that point. If the item has rolled out of the feed, check the publisher's site or any backup you already have.

## Evidence and reproduction

- [Both rounds, exact versions, image digests and observations](results.json)
- [Six original XML bodies served in the three fixture phases](fixtures/)
- [GitHub Actions run with screenshots and raw receipts](https://github.com/IcedOsmanthusLatte/brew-opml-linux-test/actions/runs/45839684983)
- [Original runner](../run_trial.py) and [synthetic origin server](../fixture_server.py)

The test used FreshRSS's `intitle:lacuna` and `intext:lacuna` auto-mark-as-read rules and Miniflux's `EntryTitle=(?i)lacuna` and `EntryContent=(?i)lacuna` block rules. These settings were saved and verified in the applications' own web forms. The selected feed's all-article and unread lists were compared with read-only database observations. There were 32 decisive full fixture fetches across both rounds. Four extra rule-free libraries were positive controls and imported all 12 entries still present across the final two feeds. The fixture recorded stable GUIDs, SHA-256, ETag, and Last-Modified. Default-cache Miniflux refresh, later retention/cleanup, mobile clients, third-party copies, and other versions are outside this experiment's scope. The OPML fixture uses a host supplied by the isolated runner.
