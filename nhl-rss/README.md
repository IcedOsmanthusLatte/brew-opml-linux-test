# NHL RSS source checks

This test used the same [six-source OPML](nhl-six-sources.opml) in separate empty Miniflux 2.3.5 libraries. The exact published file was imported into a live reader in both runs. Two other empty libraries replayed metadata captured from the same six sources to make the comparison reproducible. The archive readback verified both archive digests, the OPML bytes and the replay responses.

- First public capture — November 26, 2026 at 1:10:27 p.m. Eastern. [First reader run](https://github.com/IcedOsmanthusLatte/brew-opml-linux-test/actions/runs/45874669952)
- Second public capture — November 26, 2026 at 1:22:53 p.m. Eastern. The independent league relay added a [new NHL.com article](https://www.nhl.com/news/nhl-on-tap-news-and-notes-november-27-2026) at 1:10:47 p.m. Eastern. It was absent from the first live library and first replay, and present in the second live library and second replay. [Second reader run](https://github.com/IcedOsmanthusLatte/brew-opml-linux-test/actions/runs/45875536569)

## November 23 game day

The window ran from midnight at the start of November 23 to midnight at the start of November 24, Eastern time. The three tested to-rss subscription windows spanned both boundaries. Each of the five linked NHL originals returned HTTP 200, reported a publication time inside the fixed window and matched the canonical address shown in the reader.

| Original story | League relay | Islanders relay | Maple Leafs relay |
| --- | --- | --- | --- |
| [League projected lineups](https://www.nhl.com/news/toronto-maple-leafs-new-york-islanders-game-preview-november-23-2026) | Yes | Yes | Yes |
| [Islanders game preview](https://www.nhl.com/islanders/news/game-preview-islanders-vs-maple-leafs-nov-23-2026) | — | Yes | — |
| [Islanders postgame takeaways](https://www.nhl.com/islanders/news/takeaways-islanders-7-game-point-streak-ends-in-4-2-loss-to-maple-leafs) | — | Yes | — |
| [Maple Leafs game-day report](https://www.nhl.com/mapleleafs/news/game-day-islanders-nov-23-2026) | — | — | Yes |
| [Maple Leafs recap](https://www.nhl.com/mapleleafs/news/recap-leafs-islanders-nov-23-2026) | — | — | Yes |

The three to-rss URLs are maintained by an [independent relay](https://github.com/clokep/nhl-news-rss). They link to NHL.com originals. The CBS Sports and ESPN subscriptions came from the publishers' own domains. The tested CBS NHL address contained five URLs under clearly different sports paths in each 36-item capture. Results describe the sampled game, URLs and reader version.

[Machine-readable reader evidence](READER-EVIDENCE.json) · [Closed-window source comparison](comparison.json)
