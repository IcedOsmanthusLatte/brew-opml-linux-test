# Global News RSS test record

We imported the same [four-source OPML](global-news-four-sources.opml) into two empty
[Yarr 2.9](https://github.com/nkanaev/yarr/releases/tag/v2.9) libraries on November 27, 2026.
Each library started with zero subscriptions and zero articles. The native reader fetched the four
live addresses from the [Global News directory](https://globalnews.ca/pages/feeds/).
We re-exported the subscription list and matched every visible reader URL with the corresponding public RSS.

OPML SHA-256: `1ab40290f9ca478981c55b01b7efda9d9ba16222efcc5dabcff86a0888d831a2`.

## New articles in the second run

- [Nova Scotia school support workers ratify new deal: union](https://globalnews.ca/news/12168644/nova-scotia-school-support-workers-ratify-new-deal/) appeared in Main. RSS publication time: Nov 27 10:12:18 EST.

## Coverage in the common publication windows

For each pair, the window starts at the later of the two oldest publication times and ends
at the earlier of the two newest times. We normalize the original links and count a story once
per source. The feeds carry a rolling batch of articles; these counts describe the visible sample.
A short overlap cannot establish that a feed generally omits local stories.

### First capture — Nov 27 09:43:49 EST

Reader items by source: Main 10, National (regional menu) 10, Canada (section) 10, Calgary (local) 10.

| Compared feeds | Common window (UTC) | Visible in window | Shared URLs |
| --- | --- | --- | ---: |
| Main and National (regional menu) | Nov 27 02:59:14 UTC to Nov 27 12:30:23 UTC | 7 and 4 | 4 |
| Main and Canada (section) | Nov 27 02:59:14 UTC to Nov 27 13:20:17 UTC | 10 and 4 | 4 |
| Main and Calgary (local) | Nov 27 02:59:14 UTC to Nov 27 03:17:13 UTC | 2 and 1 | 1 |
| National (regional menu) and Canada (section) | Nov 26 23:26:12 UTC to Nov 27 12:30:23 UTC | 6 and 8 | 3 |
| National (regional menu) and Calgary (local) | Nov 26 20:57:36 UTC to Nov 27 03:17:13 UTC | 7 and 4 | 1 |
| Canada (section) and Calgary (local) | Nov 26 23:26:12 UTC to Nov 27 03:17:13 UTC | 6 and 3 | 1 |

Examples confirmed against the publisher’s original pages:

- [Kelowna hospital among longest waits for CT scans, new data reveals](https://globalnews.ca/news/12167980/kelowna-hospital-among-longest-waits-for-ct-scans-new-data-reveals/) was visible in Main only in the common window with National (regional menu).
- [School bus catches fire, rolls onto railway tracks in St. Albert](https://globalnews.ca/news/12168432/school-bus-catches-fire-rolls-onto-railway-tracks-in-st-albert/) was visible in Main only in the common window with National (regional menu).
- [Sentencing hearing to continue for Kenneth Law, who admitted to aiding suicide](https://globalnews.ca/news/12168621/kenneth-law-sentencing-hearing/) was visible in Main only in the common window with Canada (section).
- [Veterinary tranquilizer detected amid spike in Regina drug overdoses](https://globalnews.ca/news/12168338/vet-tranquilizer-found-regina-overdoses-sask/) was visible in Canada (section) only in the common window with National (regional menu).
- [Advisory council to issue antisemitism report as Jewish groups call out campus hate](https://globalnews.ca/news/12168223/antisemitism-on-campus/) was visible in National (regional menu) only in the common window with Calgary (local).

### Second capture — Nov 27 10:13:49 EST

Reader items by source: Main 10, National (regional menu) 10, Canada (section) 10, Calgary (local) 10.

| Compared feeds | Common window (UTC) | Visible in window | Shared URLs |
| --- | --- | --- | ---: |
| Main and National (regional menu) | Nov 27 03:17:13 UTC to Nov 27 12:30:23 UTC | 6 and 3 | 3 |
| Main and Canada (section) | Nov 27 03:17:13 UTC to Nov 27 13:20:17 UTC | 9 and 4 | 4 |
| Main and Calgary (local) | Nov 27 03:17:13 UTC to Nov 27 03:17:13 UTC | 1 and 1 | 1 |
| National (regional menu) and Canada (section) | Nov 26 23:26:12 UTC to Nov 27 12:30:23 UTC | 6 and 8 | 3 |
| National (regional menu) and Calgary (local) | Nov 26 20:57:36 UTC to Nov 27 03:17:13 UTC | 7 and 4 | 1 |
| Canada (section) and Calgary (local) | Nov 26 23:26:12 UTC to Nov 27 03:17:13 UTC | 6 and 3 | 1 |

Examples confirmed against the publisher’s original pages:

- [Kelowna hospital among longest waits for CT scans, new data reveals](https://globalnews.ca/news/12167980/kelowna-hospital-among-longest-waits-for-ct-scans-new-data-reveals/) was visible in Main only in the common window with National (regional menu).
- [School bus catches fire, rolls onto railway tracks in St. Albert](https://globalnews.ca/news/12168432/school-bus-catches-fire-rolls-onto-railway-tracks-in-st-albert/) was visible in Main only in the common window with National (regional menu).
- [Sentencing hearing to continue for Kenneth Law, who admitted to aiding suicide](https://globalnews.ca/news/12168621/kenneth-law-sentencing-hearing/) was visible in Main only in the common window with Canada (section).
- [Veterinary tranquilizer detected amid spike in Regina drug overdoses](https://globalnews.ca/news/12168338/vet-tranquilizer-found-regina-overdoses-sask/) was visible in Canada (section) only in the common window with National (regional menu).
- [Advisory council to issue antisemitism report as Jewish groups call out campus hate](https://globalnews.ca/news/12168223/antisemitism-on-campus/) was visible in National (regional menu) only in the common window with Calgary (local).

