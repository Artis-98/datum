<?php
// Public endpoint: GET /datum-upcoming.php
//
// What is coming next in DATUM, for datum.iiteg.com. It is the admin
// board, read live: every card under "Reddit suggestions" (task 73, under
// DATUM) that is not done or cancelled, and is urgent (the next update),
// high (soon) or normal (later), most urgent first.  Low, or none, keeps
// a request on the board but off the site, so the list of requests can
// stay long while the site shows only what is planned.
//
// Title, status and priority only. A card's description carries who
// asked and notes for us, and stays on the board.
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
        "SELECT title, status, priority FROM ws_tasks
          WHERE parent_id = ?
            AND priority IN ('urgent', 'high', 'normal')
            AND status NOT IN ('done', 'cancelled')
          ORDER BY CASE priority WHEN 'urgent' THEN 0
                                 WHEN 'high' THEN 1 ELSE 2 END,
                   sort_order, id");
    $st->execute([UPCOMING_PARENT]);
    $items = [];
    foreach ($st->fetchAll(PDO::FETCH_ASSOC) as $row) {
        $items[] = [
            'title'    => (string)$row['title'],
            'status'   => (string)$row['status'],
            'priority' => (string)$row['priority'],
        ];
    }
    echo json_encode(['upcoming' => $items], JSON_UNESCAPED_UNICODE);
} catch (Throwable $e) {
    // what went wrong is for the server log, not for the public
    error_log('datum-upcoming: ' . $e->getMessage());
    http_response_code(500);
    echo json_encode(['error' => 'unavailable']);
}
