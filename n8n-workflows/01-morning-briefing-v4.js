// ============================================================
// Morning Briefing v4 — Updated with System Pulse integration
// Cron: 0 5 * * * (daily at 5:00 AM) — America/Vancouver
// Purpose: Aggregates overnight changes from Notion, builds
//          a daily briefing, includes system health status,
//          and sends to Josh via Telegram.
// Deploy: Paste into the Code node of the Morning Briefing workflow.
// ============================================================
// FIX: Status filter uses 'Complete' and 'Archived - Obsolete'
//      (not 'Done'). Priority check uses 'P1 Critical' per schema.
// ============================================================

var NOTION_TOKEN = $env.NOTION_TOKEN;
var NOTION_TASKS_DB = '2e31cd933f028044a7e4c708809105dd';
var NOTION_DASHBOARD = '2e31cd933f028035af96ce990be06643';
var TELEGRAM_TOKEN = $env.TELEGRAM_BOT_TOKEN;
var TELEGRAM_CHAT_ID = '7729113934';
var ANTHROPIC_KEY = $env.ANTHROPIC_API_KEY;

var staticData = $getWorkflowStaticData('global');
var now = new Date();
var todayStr = now.toISOString().split('T')[0];
var briefingParts = [];

try {

  // ============================================================
  // 1. Fetch active tasks from Notion Master Tasks DB
  // ============================================================
  var tasksResponse = null;
  try {
    tasksResponse = await this.helpers.httpRequest({
      method: 'POST',
      url: 'https://api.notion.com/v1/databases/' + NOTION_TASKS_DB + '/query',
      headers: {
        'Authorization': 'Bearer ' + NOTION_TOKEN,
        'Notion-Version': '2022-06-28',
        'Content-Type': 'application/json'
      },
      body: {
        filter: {
          and: [
            {
              property: 'Status',
              status: {
                does_not_equal: 'Complete'
              }
            },
            {
              property: 'Status',
              status: {
                does_not_equal: 'Archived - Obsolete'
              }
            }
          ]
        },
        sorts: [
          {
            property: 'Priority',
            direction: 'ascending'
          }
        ],
        page_size: 50
      },
      json: true
    });
  } catch (e) {
    briefingParts.push('TASKS: Failed to fetch — ' + String(e.message || e).substring(0, 100));
  }

  // Parse tasks
  var taskCount = 0;
  var overdueCount = 0;
  var highPriorityTasks = [];
  var dueTodayTasks = [];

  if (tasksResponse && tasksResponse.results) {
    taskCount = tasksResponse.results.length;

    for (var i = 0; i < tasksResponse.results.length; i++) {
      var page = tasksResponse.results[i];
      var props = page.properties || {};

      // Extract task name
      var taskName = 'Untitled';
      if (props.Name && props.Name.title && props.Name.title.length > 0) {
        taskName = props.Name.title[0].plain_text || 'Untitled';
      } else if (props['Task Name'] && props['Task Name'].title && props['Task Name'].title.length > 0) {
        taskName = props['Task Name'].title[0].plain_text || 'Untitled';
      }

      // Check priority
      var priority = '';
      if (props.Priority && props.Priority.select) {
        priority = props.Priority.select.name || '';
      }
      if (priority === 'P1 Critical') {
        highPriorityTasks.push(taskName);
      }

      // Check due date
      var dueDate = null;
      if (props['Due Date'] && props['Due Date'].date && props['Due Date'].date.start) {
        dueDate = props['Due Date'].date.start;
      } else if (props.Due && props.Due.date && props.Due.date.start) {
        dueDate = props.Due.date.start;
      }

      if (dueDate) {
        var dueDateStr = dueDate.split('T')[0];
        if (dueDateStr === todayStr) {
          dueTodayTasks.push(taskName);
        } else if (dueDateStr < todayStr) {
          overdueCount++;
        }
      }
    }
  }

  // Build tasks section
  briefingParts.push('TASKS: ' + taskCount + ' active');
  if (overdueCount > 0) {
    briefingParts.push('OVERDUE: ' + overdueCount + ' tasks past due date');
  }
  if (dueTodayTasks.length > 0) {
    briefingParts.push('DUE TODAY:');
    for (var d = 0; d < dueTodayTasks.length; d++) {
      briefingParts.push('  - ' + dueTodayTasks[d]);
    }
  }
  if (highPriorityTasks.length > 0) {
    briefingParts.push('HIGH PRIORITY:');
    for (var h = 0; h < highPriorityTasks.length && h < 5; h++) {
      briefingParts.push('  - ' + highPriorityTasks[h]);
    }
  }

  // ============================================================
  // 2. Check for overnight changes (tasks modified since last briefing)
  // ============================================================
  var lastBriefing = staticData.last_briefing_timestamp || null;
  if (lastBriefing) {
    var changedCount = 0;
    if (tasksResponse && tasksResponse.results) {
      for (var c = 0; c < tasksResponse.results.length; c++) {
        var lastEdited = tasksResponse.results[c].last_edited_time || '';
        if (lastEdited > lastBriefing) {
          changedCount++;
        }
      }
    }
    if (changedCount > 0) {
      briefingParts.push('OVERNIGHT: ' + changedCount + ' tasks modified since last briefing');
    }
  }

  // ============================================================
  // 3. System Health — read pulse data
  // ============================================================
  var pulseData = null;
  try {
    pulseData = staticData.last_pulse || null;
  } catch (e) {
    pulseData = null;
  }

  if (pulseData) {
    if (pulseData.all_healthy) {
      briefingParts.push('SYSTEM HEALTH: All systems operational');
    } else {
      briefingParts.push('SYSTEM HEALTH: ISSUES DETECTED');
      var checkNames = Object.keys(pulseData.checks || {});
      for (var f = 0; f < checkNames.length; f++) {
        if (pulseData.checks[checkNames[f]].status === 'fail') {
          briefingParts.push('  - ' + checkNames[f] + ': DOWN');
        }
      }
    }
    if (pulseData.pulse_timestamp) {
      briefingParts.push('  Last pulse: ' + pulseData.pulse_timestamp);
    }
  } else {
    briefingParts.push('SYSTEM HEALTH: No pulse data available (System Pulse may not be forwarding to this workflow yet)');
  }

  // ============================================================
  // 4. Generate briefing narrative via Claude
  // ============================================================
  var briefingRaw = briefingParts.join('\n');
  var briefingText = '';

  try {
    var claudeResponse = await this.helpers.httpRequest({
      method: 'POST',
      url: 'https://api.anthropic.com/v1/messages',
      headers: {
        'x-api-key': ANTHROPIC_KEY,
        'anthropic-version': '2023-06-01',
        'Content-Type': 'application/json'
      },
      body: {
        model: 'claude-haiku-4-5-20251001',
        max_tokens: 500,
        messages: [
          {
            role: 'user',
            content: 'You are the Conductor, an AI operations agent for Josh Liberman (mortgage broker, 8 businesses). Write a brief, punchy morning briefing for Telegram based on this data. Keep it under 300 words. Use plain text, no markdown. Start with the date and a one-line summary.\n\nDATA:\n' + briefingRaw
          }
        ]
      },
      json: true
    });

    if (claudeResponse && claudeResponse.content && claudeResponse.content[0]) {
      briefingText = claudeResponse.content[0].text;
    } else {
      briefingText = 'MORNING BRIEFING -- ' + todayStr + '\n\n' + briefingRaw;
    }
  } catch (e) {
    briefingText = 'MORNING BRIEFING -- ' + todayStr + '\n(Claude unavailable -- raw data below)\n\n' + briefingRaw;
  }

  // ============================================================
  // 5. Send to Telegram
  // ============================================================
  try {
    await this.helpers.httpRequest({
      method: 'POST',
      url: 'https://api.telegram.org/bot' + TELEGRAM_TOKEN + '/sendMessage',
      headers: {
        'Content-Type': 'application/json'
      },
      body: {
        chat_id: TELEGRAM_CHAT_ID,
        text: briefingText,
        parse_mode: 'HTML'
      },
      json: true
    });
  } catch (e) {
    // Log but do not crash — Telegram might be down
  }

  // ============================================================
  // 6. Update static data
  // ============================================================
  staticData.last_briefing_timestamp = now.toISOString();
  staticData.last_briefing_task_count = taskCount;
  staticData.last_briefing_overdue = overdueCount;

  return [{
    json: {
      date: todayStr,
      task_count: taskCount,
      overdue_count: overdueCount,
      high_priority_count: highPriorityTasks.length,
      due_today_count: dueTodayTasks.length,
      pulse_available: pulseData !== null,
      pulse_healthy: pulseData ? pulseData.all_healthy : null,
      briefing_sent: true
    }
  }];

} catch (outerError) {
  try {
    await this.helpers.httpRequest({
      method: 'POST',
      url: 'https://api.telegram.org/bot' + TELEGRAM_TOKEN + '/sendMessage',
      headers: {
        'Content-Type': 'application/json'
      },
      body: {
        chat_id: TELEGRAM_CHAT_ID,
        text: 'MORNING BRIEFING FAILED at ' + now.toISOString() + ': ' + String(outerError.message || outerError).substring(0, 500),
        parse_mode: 'HTML'
      },
      json: true
    });
  } catch (e) {
    // Cannot alert
  }

  return [{
    json: {
      date: todayStr,
      error: String(outerError.message || outerError),
      briefing_sent: false
    }
  }];
}