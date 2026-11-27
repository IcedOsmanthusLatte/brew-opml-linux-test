# Raindrop RSS in a fresh Yarr library

We imported the same [four-source OPML](raindrop-four-sources.opml) into two separate empty Yarr 2.9 libraries on Windows on November 27, 2026 at 15:51:58 and 15:52:01 UTC. We read every page of the native item lists, matched the visible links and save times against the public RSS, and re-exported four subscriptions from each library. Both runs returned the same counts. All four RSS responses matched the import snapshots byte for byte when checked again on November 27 at 17:39 UTC. [Yarr 2.9](https://github.com/nkanaev/yarr/releases/tag/v2.9)

| Public collection | RSS items | Distinct RSS links | Yarr items per run |
| --- | ---: | ---: | ---: |
| [AI](https://mguhlin.raindrop.page/ai-57580928/feed), parent | 50 | 50 | 50 |
| [Education AI](https://mguhlin.raindrop.page/education-ai-60564646/feed), child | 50 | 50 | 50 |
| [Stellar Web Links](https://smithographic.raindrop.page/stellar-web-links-54448211/feed), parent | 50 | 49 | 49 |
| [Apps](https://smithographic.raindrop.page/apps-52725232/feed), child | 48 | 48 | 48 |

- **Follow the child for more of its topic.** Education AI showed 31 links missing from the sampled AI feed, and Apps showed 46 missing from the sampled Stellar Web Links feed. Each was saved before the oldest item visible in its corresponding parent feed. Apps is a historical sample. [Miguel's collections](https://mguhlin.org/blog/curating-ai-with-raindrop/) · [Smithographics' collection list](https://imjustcreative.com/bookmarks)
- **Expect overlap when following both.** AI and Education AI shared 19 links. Stellar Web Links and Apps shared two. Yarr kept a separate item per feed for those shared links. One repeated GUID and URL within Stellar Web Links appeared once in Yarr. Across the four feeds, each import showed 197 reader items for 176 distinct links.
- **The feed date is the save date.** Raindrop's public RSS code requests up to 50 records in descending creation order, uses each record's creation time for `pubDate`, and queries nested collections by default. That is the bookmark's save time. [Raindrop RSS help](https://help.raindrop.io/integrations/rss) · [RSS implementation](https://raw.githubusercontent.com/raindropio/pages/refs/heads/release/production/src/server/feed/index.js) · [Nested collection query](https://raw.githubusercontent.com/raindropio/pages/refs/heads/release/production/src/api/raindrops/get.js)

These results cover the first import of two public hierarchies into Yarr 2.9. The feeds reflect the bookmarks visible at the check time. The experiment measured the initial result, including its older links and overlap.

OPML SHA-256 `bc4f581be2d38ef4990b34de875972e6e9c973eb764be7b66407207807b050e3`
