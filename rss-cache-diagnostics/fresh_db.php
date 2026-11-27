<?php
declare(strict_types=1);
require '/var/www/FreshRSS/cli/_cli.php';

$username = cliInitUser('filterlab');
$dao = FreshRSS_Factory::createFeedDao($username);
$feeds = [];
foreach ($dao->listFeeds() as $feed) {
    $filters = array_map(static fn($f) => $f->toString(), $feed->filtersAction('read'));
    $feeds[] = ['id' => $feed->id(), 'url' => $feed->url(false), 'filters' => $filters];
}

$database = new PDO('sqlite:' . USERS_PATH . '/' . $username . '/db.sqlite');
$database->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
$database->exec('PRAGMA query_only = ON');
$table = $database->query("SELECT name FROM sqlite_master WHERE type='table' AND (name='entry' OR name LIKE '%_entry')")->fetchColumn();
if (!is_string($table) || preg_match('/^[A-Za-z0-9_]+$/', $table) !== 1) {
    fail('FreshRSS entry table was not found.');
}
$rows = $database->query("SELECT guid, title, link, is_read, date FROM $table ORDER BY link")->fetchAll(PDO::FETCH_ASSOC);
echo json_encode(['version' => FRESHRSS_VERSION, 'feeds' => $feeds, 'rows' => $rows], JSON_THROW_ON_ERROR);
