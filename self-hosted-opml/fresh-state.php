<?php
declare(strict_types=1);

require '/var/www/FreshRSS/cli/_cli.php';
$username = cliInitUser('opmllab');
$dao = FreshRSS_Factory::createFeedDao($username);
$target = $argv[1] ?? null;
$updated = false;
foreach ($dao->listFeeds() as $feed) {
    if ($target !== null && $feed->url(false) === $target) {
        $feed->_mute(true);
        if (!$dao->updateFeed($feed->id(), ['ttl' => $feed->ttl(raw: true)])) {
            fail('FreshRSS DAO did not persist the mute change.');
        }
        $updated = true;
    }
}
if ($target !== null && !$updated) {
    fail('FreshRSS did not find the target URL.');
}
$result = [];
foreach ($dao->listFeeds() as $feed) {
    $result[] = [
        'url' => $feed->url(false),
        'title' => $feed->name(),
        'folder' => $feed->category()?->name(),
        'paused' => $feed->mute(),
        'ttl_raw_seconds' => $feed->ttl(raw: true),
    ];
}
echo json_encode(['version' => FRESHRSS_VERSION, 'feeds' => $result], JSON_THROW_ON_ERROR);
