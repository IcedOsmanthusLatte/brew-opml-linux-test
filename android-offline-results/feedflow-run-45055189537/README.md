# FeedFlow 1.21.0 on Android 15

[GitHub-hosted run and artifact](https://github.com/IcedOsmanthusLatte/brew-opml-linux-test/actions/runs/45055189537) · [Exact test scripts](https://github.com/IcedOsmanthusLatte/brew-opml-linux-test/tree/2c7244a7a1e3441f2fbdd35522ff2fa895f88058/android-offline-runtime) · [OPML used for all routes](https://raw.githubusercontent.com/IcedOsmanthusLatte/brew-opml-linux-test/2c7244a7a1e3441f2fbdd35522ff2fa895f88058/android-offline-runtime/control.opml)

Each route started with clean app data and imported the same OPML in FeedFlow. The reader opened the selected items for the first time after the app restarted in airplane mode with Wi-Fi and mobile data disabled. The independent Android HTTPS probe succeeded online, failed before and after the first opens, and succeeded again after reconnection. APK provenance and the identical OPML hash are in the accompanying JSON files.

| Setting before refresh | BBC article, both repeats | WordPress article, both repeats |
| --- | --- | --- |
| Default | Only feed excerpt markers observed | Text matched the body already supplied in RSS |
| Make offline copies when feeds refresh | Nine origin-only text markers observed each time | Text matched the body already supplied in RSS |

The [BBC item](https://www.bbc.co.uk/news/articles/cj752ddve27ko) contained a 19-word excerpt and no encoded body in the feed. The [WordPress item](https://wordpress.org/news/2026/11/global-partners-third-quarter-2026/) contained 659 words in its encoded RSS body. The same title, URL, and GUID hashes matched across all four clean routes. Offline cutoffs followed refresh by 70.6–71.4 seconds in these runs. The JSON holds digests and markers only.

The positive result establishes that this prefetch setting made part of the BBC publisher's article beyond the excerpt readable on first offline open in this test. The marker check gives a bounded observation of readable text on one Android build and two selected items.
