# Windows RSS reader move: OPML and article flag recovery

Tested on GitHub-hosted Microsoft Windows Server 2025 Datacenter x64, using FeedFlow 1.21.0 and Fluent Reader 1.3.2. Both released package hashes are in [environment.json](environment.json). Every source case imported the same [public three-feed OPML](https://getbrew.app/collections/downloads/opml-migration-normal.opml), SHA-256 `822295536b4776a25750d7dc4b707afff1fe9086d33382e6148b984195ed1afd`.

Each of two rounds used clean profiles and the same three feed/article pairs in both apps: one read, one saved, and one unchanged. The FeedFlow fixture flags were set in two exact rows in the closed, disposable source profile after importing and refreshing the real feeds. We reopened the released app and verified the flags. The apps performed all native exports and restores through their normal UI. Destination database inspection was read-only.

| Route | Observed result in both rounds |
| --- | --- |
| Fluent Reader OPML only | Three subscriptions and the three matched articles were present after refresh. Neither flag returned. |
| Fluent Reader original `.frdata` backup and Restore | Both intended flags returned and stayed set after restart. |
| FeedFlow OPML only | Three subscriptions and the three matched articles were present after refresh. Neither flag returned. |
| FeedFlow OPML then article CSV | Both intended flags returned and stayed set after restart. |
| FeedFlow CSV first in an empty profile | The UI displayed **Articles import done**. There were zero subscriptions and zero articles. Adding OPML alone left the flags unset. Reimporting the exact same CSV restored both flags and they stayed set after restart. |

The third article kept both flags unset in all routes where it existed. The results measure article flags on the tested versions. Network isolation was unverified, so these results do not establish offline restoration or coverage of all article content.

- [FeedFlow two-round status snapshots](feedflow-summary.json)
- [Fluent Reader two-round status snapshots](fluent-summary.json)
- [Successful GitHub Actions run](https://github.com/IcedOsmanthusLatte/brew-opml-linux-test/actions/runs/45042810100) and [original evidence archive](https://github.com/IcedOsmanthusLatte/brew-opml-linux-test/actions/runs/45042810100/artifacts/14762702231)
- Archive SHA-256 `26c5441b21f3efd1c65e639c6b0286224d9286ca35aa40d9a51aa5b5326a70b1`
