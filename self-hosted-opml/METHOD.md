# FreshRSS and Miniflux OPML settings round trip

This experiment compares subscription addresses, category membership, and one subscription's pause state on FreshRSS 1.30.4 and Miniflux 2.3.5. The workflow runs twice on disposable Docker instances on a GitHub-hosted Ubuntu runner. Every source instance uses the checked-in, byte-identical copy of the published OPML with its fixed SHA-256. The two unpaused subscriptions are controls.

FreshRSS uses the official import and export CLI commands and its native mute model plus feed DAO for state changes and readback. Miniflux uses the official API to import, edit the target subscription, export and read back. Each native OPML export goes into both a new instance of its source app and a new instance of the other app. No exported credentials or generated test accounts appear in evidence. The app versions and container image digests are part of the artifact.

FreshRSS 1.30.4's CLI and browser normal OPML download both call the same `generateOpml()` routine. This routine includes app-specific details. The experiment uses the CLI entry point and does not exercise browser controls. Miniflux uses one OPML export API endpoint. The scope is OPML subscription transfer. Whole-database recovery, article history, fetch timing and cross-version behavior are outside the experiment.
