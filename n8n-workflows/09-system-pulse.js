// ============================================================
// System Pulse — INF-090
// Cron: 0 */30 * * * * (every 30 min) — America/Vancouver
// Purpose: Health check for Notion, Telegram, Claude, and n8n.
//          Silent when healthy. Sends Telegram alert on failure.
// Deploy: Paste into a single n8n Code node in a new workflow.
// ============================================================
// AUDIT 2026-04-12: Fixed $helpers -> this.helpers, inlined all
// standalone helper functions (timedRequest, sendAlert).
// ============================================================

var NOTION_TOKEN = $env.NOTION_TOKEN;
var NOTION_DB_ID = '2e31cd933f028044a7e4c708809105dd';
var TELEGRAM_TOKEN = $env.TELEGRAM_BOT_TOKEN;
var TELEGRAM_CHAT_ID = '7729113934';
var ANTHROPIC_KEY = $env.ANTHROPIC_API_KEY;

var staticData = $getWorkflowStaticData('global');
var now = new Date().toISOString();
var checks = {};
var allHealthy = true;

try {

  // --- CHECK 1: Notion API ---
  try {
    var notionStart = Date.now();
    var notionResp = await this.helpers.httpRequest({
      method: 'GET',
      url: 'https://api.notion.com/v1/databases/' + NOTION_DB_ID,
      headers: {
        'Authorization': 'Bearer ' + NOTION_TOKEN,
        'Notion-Version': '2022-06-28'
      },
      json: true
    });
    checks.notion = { status: 'ok', response_ms: Date.now() - notionStart };
  } catch (e) {
    checks.notion = { status: 'fail', error: String(e.message || e).substring(0, 200) };
    allHealthy = false;
  }

  // --- CHECK 2: Telegram API ---
  try {
    var telegramStart = Date.now();
    var telegramResp = await this.helpers.httpRequest({
      method: 'GET',
      url: 'https://api.telegram.org/bot' + TELEGRAM_TOKEN + '/getMe',
      json: true
    });
    checks.telegram = { status: 'ok', response_ms: Date.now() - telegramStart };
  } catch (e) {
    checks.telegram = { status: 'fail', error: String(e.message || e).substring(0, 200) };
    allHealthy = false;
  }

  // --- CHECK 3: Claude API ---
  try {
    var claudeStart = Date.now();
    var claudeResp = await this.helpers.httpRequest({
      method: 'POST',
      url: 'https://api.anthropic.com/v1/messages',
      headers: {
        'x-api-key': ANTHROPIC_KEY,
        'anthropic-version': '2023-06-01',
        'Content-Type': 'application/json'
      },
      body: {
        model: 'claude-haiku-4-5-20251001',
        max_tokens: 1,
        messages: [{ role: 'user', content: 'ping' }]
      },
      json: true
    });
    checks.claude = { status: 'ok', response_ms: Date.now() - claudeStart };
  } catch (e) {
    checks.claude = { status: 'fail', error: String(e.message || e).substring(0, 200) };
    allHealthy = false;
  }

  // --- CHECK 4: VPS disk ---
  // Cannot use require('fs') in n8n sandbox. Skipping disk check.
  // If disk monitoring is needed, use a separate n8n Execute Command node.

  // --- CHECK 5: n8n heartbeat ---
  try {
    var previousTimestamp = staticData.last_heartbeat || null;
    var gapMinutes = null;

    if (previousTimestamp) {
      var prev = new Date(previousTimestamp).getTime();
      var current = new Date(now).getTime();
      gapMinutes = Math.round((current - prev) / 60000);
    }

    checks.n8n_heartbeat = {
      status: 'ok',
      gap_minutes: gapMinutes
    };

    // If gap is more than 45 minutes (30 min schedule + 15 min tolerance), flag it
    if (gapMinutes !== null && gapMinutes > 45) {
      checks.n8n_heartbeat.status = 'warn';
      checks.n8n_heartbeat.note = 'Gap of ' + gapMinutes + ' minutes detected (expected ~30)';
    }

    staticData.last_heartbeat = now;
  } catch (e) {
    checks.n8n_heartbeat = { status: 'fail', error: String(e.message || e).substring(0, 200) };
    allHealthy = false;
  }

  // --- Store results in static data for Morning Briefing ---
  var pulseResult = {
    pulse_timestamp: now,
    checks: checks,
    all_healthy: allHealthy
  };

  staticData.last_pulse = pulseResult;

  // --- Send Telegram alert ONLY if something failed ---
  if (!allHealthy) {
    var failedChecks = [];
    var checkNames = Object.keys(checks);
    for (var i = 0; i < checkNames.length; i++) {
      var name = checkNames[i];
      if (checks[name].status === 'fail') {
        failedChecks.push('- <b>' + name + '</b>: ' + (checks[name].error || 'unknown error'));
      }
    }

    var alertMsg = '<b>SYSTEM PULSE ALERT</b>\n\n'
      + 'One or more health checks failed at ' + now + ':\n\n'
      + failedChecks.join('\n')
      + '\n\nCheck VPS and service status.';

    try {
      await this.helpers.httpRequest({
        method: 'POST',
        url: 'https://api.telegram.org/bot' + TELEGRAM_TOKEN + '/sendMessage',
        body: {
          chat_id: TELEGRAM_CHAT_ID,
          text: alertMsg,
          parse_mode: 'HTML'
        },
        headers: {
          'Content-Type': 'application/json'
        },
        json: true
      });
    } catch (e) {
      // If Telegram itself is down, we cannot alert
    }
  }

  return [{ json: pulseResult }];

} catch (outerError) {
  // Catastrophic failure — try to alert via Telegram
  var errorMsg = '<b>SYSTEM PULSE CRASHED</b>\n\n'
    + 'The pulse workflow itself failed at ' + now + ':\n'
    + String(outerError.message || outerError).substring(0, 500);

  try {
    await this.helpers.httpRequest({
      method: 'POST',
      url: 'https://api.telegram.org/bot' + TELEGRAM_TOKEN + '/sendMessage',
      body: {
        chat_id: TELEGRAM_CHAT_ID,
        text: errorMsg,
        parse_mode: 'HTML'
      },
      headers: {
        'Content-Type': 'application/json'
      },
      json: true
    });
  } catch (e) {
    // Cannot alert
  }

  return [{ json: { pulse_timestamp: now, checks: {}, all_healthy: false, error: String(outerError.message || outerError) } }];
}
