# Mac RSS reader OPML round-trip evidence

This experiment compares folder paths exported by NetNewsWire for Mac 7.3.1 and Vienna 3.10.8. The signed publisher releases ran on GitHub-hosted Apple silicon under macOS 15. In each trial the local library was cleared through the app's native interface before opening the OPML import dialog. The app was restarted, the sidebar checked, and its normal export command used to save the result. Vienna's export preserves groups. Each condition was assigned two independent virtual machines.

## Observed folder paths

All three feed URLs appeared exactly once in each of the 12 native exports. Both apps preserved the paths in the original one-level sample. The directory results from the identical nested input were reproducible and differed by app.

| Feed | Input | NetNewsWire export | Vienna export |
| --- | --- | --- | --- |
| BBC News — World | Brew Alpha / Brew Shared | Brew Alpha | Brew Alpha / Brew Shared |
| NASA News Releases | Brew Beta / Brew Shared | Brew Beta | Brew Alpha / Brew Shared |
| WordPress News | Brew Shallow | Brew Shallow | Brew Shallow |

Vienna also exported the empty `Brew Beta` folder. The two adapted inputs below preserved every tested path in their respective apps.

## Inputs

- **Baseline** uses the original Brew three-feed sample byte for byte. [Original OPML](https://getbrew.app/collections/downloads/opml-migration-normal.opml)
- **Nested** uses the same three feed URLs and per-feed metadata. BBC News — World and NASA News Releases are under two different parent folders that each contain a child named `Brew Shared`. WordPress News is a shallow reference under `Brew Shallow`.
- **Flattened** gives NetNewsWire one level of uniquely named folders. The parent and child names remain visible as `Brew Alpha - Brew Shared` and `Brew Beta - Brew Shared`.
- **Unique** keeps Vienna's two levels but gives the child folders distinct names, `Brew Shared A` and `Brew Shared B`.

## Verification

Each trial directory holds the exact input, the app's exported XML, the verified release and macOS details, and sidebar transcripts from before import, just after import, and after relaunch. The machine-readable comparison includes the full directory path for every URL, and empty groups are retained in its group listing. The cleared library screenshot is included. The other raw screenshots may include unrelated syndicated article content, so their hashes are recorded. The receipt identifies the complete original GitHub Actions archive and its SHA-256. Actions archives have a 14-day retention period. [Actions workflow](https://github.com/IcedOsmanthusLatte/brew-opml-linux-test/actions/workflows/mac-opml-runtime.yml)

From this directory, run `python3 verify_native_rounds.py trials/*` in a Unix shell to recalculate the comparison from the trial records.

The files measure how the tested local libraries retain feed subscriptions and folders. Read state, saved items, sync accounts and article content are outside the experiment.
