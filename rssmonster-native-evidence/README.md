# RSSMonster 2.3.0 OPML category round trip

We ran the published RSSMonster image in isolated Ubuntu 24.04 jobs with fresh SQLite libraries. We checked that its image revision matches the 2.3.0 release commit `e6ad401e893763db7e123421a7a1128152b0563d`. The image index digest was `sha256:cbd8f34d81a5ec57d069fdb39897c1059b242fdcec16f41a5e17a70bb35cb5c0`. [Official release](https://github.com/pietheinstrengholt/rssmonster/releases/tag/v2.3.0) · [Four isolated round trips](https://github.com/IcedOsmanthusLatte/brew-opml-linux-test/actions/runs/45364476893)

| Input | Native export | Result |
| --- | --- | --- |
| [One level of categories](flat-input.opml) | [Flat export](flat-export.opml) | All three feed addresses and all their category paths stayed the same. |
| [Nested categories](nested-input.opml) | [Default export 1](nested-export-1.opml), [default export 2](nested-export-2.opml) | Both new libraries kept all three addresses. BBC and NASA started under different parents with the same child name. Both appeared under one single-level `Brew Shared` category. WordPress stayed in `Brew Shallow`. |
| [Same nested input](nested-input.opml) | [Edited category API export](edited-api-export.opml), [web interface export](edited-ui-export.opml) | We assigned BBC and NASA distinct labels in the import preview. The exported OPML kept all three addresses and both distinct one-level categories. |

The nested preview showed the shared category before the import. An independent headless browser run used the released interface’s per-feed editor to assign `Brew Alpha - Brew Shared` and `Brew Beta - Brew Shared`, confirmed the import, then used **Export OPML** to download the checked file. [Web interface run](https://github.com/IcedOsmanthusLatte/brew-opml-linux-test/actions/runs/45365163597) · [Official import and export guide](https://pietheinstrengholt.github.io/rssmonster/opml.html)

When original grouping matters, review the **Category** column before importing. Distinct one-level labels can keep sources separate. Keep the original OPML, then compare the exported file to confirm addresses and categories. These files test feed addresses and categories for this version. Other settings and article content are outside the test.

[File checksums](SHA256SUMS)
