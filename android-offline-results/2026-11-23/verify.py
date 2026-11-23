"""Audit sanitized Android evidence from the public Actions matrix."""
import argparse
import json
from pathlib import Path

FIXTURE = "822295536b4776a25750d7dc4b707afff1fe9086d33382e6148b984195ed1afd"
PACKAGES = {"com.nononsenseapps.feeder": ("4191", "2.25.0"), "com.prof18.feedflow": ("6239", "1.21.0")}
ROUTES = {"baseline", "prefetch"}
ROUNDS = {1, 2}


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def network(online, offline):
    assert online["http_status"] == 200 and online["any_active_network"]
    assert offline["http_status"] == 0 and not offline["any_active_network"]
    assert offline["airplane_mode"] == "1"
    assert offline["wifi_setting"] == "0"
    assert offline["default_mobile_data_enabled"] is False and offline["mobile_data_connection_allowed"] is False
    assert not any(offline[key] for key in ("wifi", "cellular", "validated"))
    assert online["nonce"] != offline["nonce"]


def verify_job(directory, package, round_num, routes=ROUTES):
    expected = PACKAGES[package]
    hardware = load(directory / "host.json")
    guest = hardware["avd"]
    assert guest["api"] == "35" and guest["abi"] == "x86_64"
    assert guest["fixture_sha256_in_guest"] == FIXTURE
    install = guest["installed_apps"][package]
    assert (install["version_code"], install["version"]) == expected
    network(hardware["online"], hardware["offline"])
    network(hardware["restored"], hardware["offline"])
    provenance = load(directory / "apk-builds.json")
    assert provenance["fixture_sha256"] == FIXTURE
    assert len(provenance["fdroid_signed_index_sha256"]) == 64
    build = provenance["packages"][package]
    assert (str(build["version_code"]), build["version"]) == expected
    assert len(build["apk_sha256"]) == len(build["apk_signer_sha256"]) == 64
    navigation = load(directory / "native-navigation.json")
    traces = {}
    for row in navigation:
        if row["step"] == "begin-clean-profile":
            assert (row["package"], row["round"]) == (package, round_num)
            traces[row["condition"]] = []
        if traces:
            traces[next(reversed(traces))].append(row)
    results = []
    for route in sorted(routes):
        trace = traces[route]
        imports = [row for row in trace if row["step"] == "native-fixture-import" and row["package"] == package]
        switch, = [row for row in trace if row["step"] == "verified-offline-setting" and row["package"] == package]
        assert len(imports) == 1 and not switch["initial_enabled"]
        assert switch["enabled"] == (route == "prefetch")
        if package == "com.nononsenseapps.feeder":
            verified_exports = [row for row in trace if row["step"] == "feeder-native-export-readback"]
            assert [row["full_text_enabled"] for row in verified_exports] == (
                [False, True] if route == "prefetch" else [False])
            assert all(row["sources"] == 3 for row in verified_exports)
        raw = load(directory / f"{package.split('.')[-1]}-{route}-{round_num}.json")
        assert (raw["package"], raw["route"], raw["round"]) == (package, route, round_num)
        assert raw["native_first_open_no_online_open"]
        network(raw["online_initial"], raw["offline_before_first_open"])
        network(raw["reconnected"], raw["offline_after_first_open"])
        assert len({raw[key]["nonce"] for key in (
            "online_initial", "offline_before_first_open", "offline_after_first_open", "reconnected"
        )}) == 4
        assert set(raw["items"]) == {"BBC", "WordPress"}
        for source, item in raw["items"].items():
            assert item["source"] == source and item["title_observed"]
            assert item["resumed_activity"].startswith(package + "/")
            if source == "BBC":
                assert item["bbc_origin_validated"]
                assert item["feed_encoded_words"] < 35
                if item["kind"] == "origin-body-beyond-feed":
                    assert len(item["origin_marker_sha256"]) >= 2
            if item["kind"] == "feed-provided-body":
                assert len(item["feed_body_marker_sha256"]) >= 2
        results.append(raw)
    return results


def compare(path, feedflow_path=None, feeder_baseline_path=None):
    all_results = []
    for package in PACKAGES:
        if package == "com.prof18.feedflow":
            batches = [(feedflow_path or path, ROUTES)]
        elif feeder_baseline_path is not None:
            batches = [(feeder_baseline_path, {"baseline"}), (path, {"prefetch"})]
        else:
            batches = [(path, ROUTES)]
        for round_num in sorted(ROUNDS):
            for artifact_root, routes in batches:
                directory = artifact_root / f"android-offline-evidence-{package}-r{round_num}"
                all_results.extend(verify_job(directory, package, round_num, routes))

    source_match = {}
    for raw in all_results:
        for source, item in raw["items"].items():
            fingerprint = tuple(item[key] for key in ("title_sha256", "url_sha256", "guid_sha256"))
            assert source_match.setdefault(source, fingerprint) == fingerprint
    assert set(source_match) == {"BBC", "WordPress"}, "The control sample drifted across apps"

    observed = {}
    for package in PACKAGES:
        observed[package] = {}
        for route in ROUTES:
            rows = sorted((x for x in all_results if x["package"] == package and x["route"] == route),
                          key=lambda x: x["round"])
            for source in source_match:
                a, b = (x["items"][source] for x in rows)
                assert a["kind"] == b["kind"], "Replicates disagree"
                if a["kind"] == "origin-body-beyond-feed":
                    assert len(set(a["origin_marker_sha256"]) & set(b["origin_marker_sha256"])) >= 2
                observed[package].setdefault(route, {})[source] = a["kind"]
    actionable = any(m["baseline"]["BBC"] != "origin-body-beyond-feed"
                     and m["prefetch"]["BBC"] == "origin-body-beyond-feed" for m in observed.values())
    default_differences = [source for source in source_match
                           if len({m["baseline"][source] for m in observed.values()}) > 1]
    return {"routes_verified": len(all_results), "samples_matched": sorted(source_match),
            "reproducible_origin_prefetch_effect": actionable, "default_differences": default_differences,
            "observations": observed,
            "evidence_runs": {"feeder_baseline": (feeder_baseline_path or path).name,
                              "feeder_prefetch": path.name, "feedflow": (feedflow_path or path).name}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", type=Path)
    parser.add_argument("--feedflow-artifacts", type=Path)
    parser.add_argument("--feeder-baseline-artifacts", type=Path)
    args = parser.parse_args()
    print(json.dumps(compare(args.artifacts, args.feedflow_artifacts, args.feeder_baseline_artifacts), indent=2))
