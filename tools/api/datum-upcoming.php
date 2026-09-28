<?php
// Public endpoint: GET /datum-upcoming.php
//
// What is coming next in DATUM, for datum.iiteg.com. It is the admin
// board, read live: every card under "Reddit suggestions" (task 73, under
// DATUM) that is flagged urgent and not done yet. Setting a card to any
// other priority, or finishing it, takes it off the site, so the list of
// requests can stay long while the site shows only what is next.
//
// Titles and status only. A card's description carries who asked and
// notes for us, and stays on the board.
//
// Lives in the DATUM repository at tools/api/, uploaded to the root of
// api.iiteg.com beside spectrum.php, whose config.php it shares.

header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: GET, OPTIONS');
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: public, max-age=60');
if (($_SERVER['REQUEST_METHOD'] ?? '') === 'OPTIONS') {
    http_response_code(204);
    exit;
}
if (($_SERVER['REQUEST_METHOD'] ?? 'GET') !== 'GET') {
    http_response_code(405);
    echo json_encode(['error' => 'GET only']);
    exit;
}

require_once __DIR__ . '/config.php';

// the card whose subtasks are the requests
const UPCOMING_PARENT = 73;

try {
    $st = db()->prepare(
        "SELECT title, status FROM ws_tasks
          WHERE parent_id = ? AND priority = 'urgent' AND status <> 'done'
          ORDER BY sort_order, id");
    $st->execute([UPCOMING_PARENT]);
    $items = [];
    foreach ($st->fetchAll(PDO::FETCH_ASSOC) as $row) {
        $items[] = [
            'title'  => (string)$row['title'],
            'status' => (string)$row['status'],
        ];
    }
    echo json_encode(['upcoming' => $items], JSON_UNESCAPED_UNICODE);
} catch (Throwable $e) {
    // what went wrong is for the server log, not for the public
    error_log('datum-upcoming: ' . $e->getMessage());
    http_response_code(500);
    echo json_encode(['error' => 'unavailable']);
}
