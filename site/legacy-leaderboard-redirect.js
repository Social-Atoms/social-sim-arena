// Preserve the old URL while keeping one canonical leaderboard UI.
// The detailed legacy renderer remains in leaderboard.html for archived data
// and contract tests, but participants are routed into the shared app shell.
location.replace('index.html#live');
