# ABC News RSS source comparison

We imported the same three-feed OPML into two empty Yarr 2.9 libraries on Windows.
Each library started with zero feeds and items. Both used direct publisher URLs. Each reader item was cross-checked to the corresponding public RSS by original URL, title and date.

- [Official feed directory](https://abcnews.com/Site/page/rss-feeds-3520115)
- [World News Headlines control](https://abcnews.com/abcnews/worldnewsheadlines)
- [International Headlines](https://abcnews.com/abcnews/internationalheadlines)
- [Top Stories](https://abcnews.com/abcnews/topstories)

OPML SHA-256: `d9b40590ada0612e6099ec60471b91d658c264b8dbdd71d90e82c00e74688929`.

## Native readings

| Capture time (UTC) | World News Headlines | International Headlines | Top Stories |
| --- | ---: | ---: | ---: |
| 2026-11-27T15:48:17.968399+00:00 | 0 | 25 | 25 |
| 2026-11-27T16:20:54.389606+00:00 | 0 | 25 | 25 |

Both World News Headlines responses were HTTP 200 and parseable RSS. The reader kept the subscription and displayed zero entries in these two checks. This does not establish whether it is permanently inactive.

## Comparing the active feeds

We compare only each pair's shared publication window. Totals below exclude articles outside that window.

- Round A: 2026-11-26T13:30:30+00:00 to 2026-11-27T15:12:33+00:00 UTC. Top Stories 11, International Headlines 24, shared original addresses 2. Matched article IDs also recorded 2 overlapping items.
  - International-only original checked against the publisher: https://abcnews.com/International/wireStory/armed-group-kills-13-soldiers-western-niger-attack-138477943
  - International-only original checked against the publisher: https://abcnews.com/International/wireStory/death-toll-pakistan-building-collapse-rises-8-10-138480840
  - International-only original checked against the publisher: https://abcnews.com/International/wireStory/ukraine-loan-limbo-after-eu-budget-talks-stall-138478913
- Round B: 2026-11-26T13:30:30+00:00 to 2026-11-27T16:10:28+00:00 UTC. Top Stories 11, International Headlines 23, shared original addresses 2. Matched article IDs also recorded 2 overlapping items.
  - International-only original checked against the publisher: https://abcnews.com/International/wireStory/armed-group-kills-13-soldiers-western-niger-attack-138477943
  - International-only original checked against the publisher: https://abcnews.com/International/wireStory/death-toll-pakistan-building-collapse-rises-8-10-138480840
  - International-only original checked against the publisher: https://abcnews.com/International/wireStory/peacekeepers-preparing-leave-southern-lebanon-plan-138476701
  - International-only original checked against the publisher: https://abcnews.com/International/wireStory/ukraine-loan-limbo-after-eu-budget-talks-stall-138478913

A newer International original first observed in the second run:

- https://abcnews.com/International/wireStory/canadas-supreme-court-largely-upholds-quebec-ban-religious-138482310 — 2026-11-27T16:18:25+00:00
- https://abcnews.com/International/wireStory/lulas-choice-bradesco-banker-brazils-finance-minister-lifts-138482032 — 2026-11-27T16:18:32+00:00

The sampled International RSS supplied descriptions and metadata. There was no `content:encoded` field in the items. For the full story, open the original page. This does not predict publisher coverage over longer periods.
