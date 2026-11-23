# Feeder and FeedFlow first offline open on Android 15

We used Android 15 (API 35, x86_64) in GitHub Actions with F-Droid releases Feeder 2.25.0 (4191) and FeedFlow 1.21.0 (6239). Each test started with a clean app library and imported the same three subscriptions. The OPML SHA-256 inside Android is `822295536b4776a25750d7dc4b707afff1fe9086d33382e6148b984195ed1afd`.

The [BBC item](https://www.bbc.co.uk/news/articles/cj752ddve27ko) carried a 19-word excerpt in its feed. The [WordPress item](https://wordpress.org/news/2026/11/global-partners-third-quarter-2026/) had a body in RSS. The experiment matches the title, URL and GUID digests of these articles across both apps. Counts and text digests are retained for reproduction.

Each test refreshed online, stopped the reader, turned on airplane mode and disabled Wi-Fi and mobile data. A fresh HTTPS probe inside Android checked the connection before and after the offline reads and on reconnection. The BBC body category requires at least two text fragments found on the publisher's page and absent from RSS. The outcome only covers the matching text we observed on the first offline open.

The two [FeedFlow jobs](https://github.com/IcedOsmanthusLatte/brew-opml-linux-test/actions/runs/45056903029) and the two [Feeder default routes](https://github.com/IcedOsmanthusLatte/brew-opml-linux-test/actions/runs/45114998821) supplied the completed evidence for their respective settings. The full check reads each saved route separately, including its clean-library import, setting and network records.

The [Feeder full-article jobs](https://github.com/IcedOsmanthusLatte/brew-opml-linux-test/actions/runs/45117554900) first imported the unchanged control. They then used Feeder's native importer to change one setting on the BBC subscription. A fresh Feeder export verified the setting and all three source URLs before the offline test. These jobs used [the exact runtime and OPML](https://github.com/IcedOsmanthusLatte/brew-opml-linux-test/tree/e2d1b269ff195efae30015c0925f37744549f3cd/android-offline-runtime).

In both apps, the two default rounds showed the BBC RSS excerpt. Both enabled rounds showed BBC text beyond RSS. Every round matched text from the WordPress RSS body. Run the verifier from this directory to check the eight saved routes.

```sh
python verify.py 45117554900 --feedflow-artifacts 45056903029 --feeder-baseline-artifacts 45114998821
```
