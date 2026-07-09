/**
 * ArtHouse Operations & Lead-Triage web app — backend.
 *
 * This Apps Script is BOUND to the arthouse-ops Google Sheet
 * (open the sheet -> Extensions -> Apps Script, then paste this in).
 * It serves the dashboard HTML and hands the front-end only the data it needs:
 * top-line KPIs, quarterly trend, category counts, and the ~140 ACTIONABLE
 * messages (never all 18k rows, and never the raw sheet to the browser).
 */

var LEADS_TAB = 'leads';
var LEAD_CATEGORIES = ['sponsor', 'school', 'volunteer']; // the "real lead" buckets

// ---- Access gate -----------------------------------------------------------
// The app returns NO data unless the viewer supplies this password. Because the
// check happens here on the server, someone with just the URL gets nothing.
// CHANGE this before deploying, then share the URL + password only with Julie.
var ACCESS_PASSWORD = 'CHANGE-ME-BEFORE-DEPLOY';

/** Serve the web app. */
function doGet() {
  return HtmlService.createTemplateFromFile('Index')
    .evaluate()
    .setTitle('ArtHouse Operations')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.DEFAULT);
}

/** Read the leads tab into an array of row objects keyed by header name. */
function readLeads_() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(LEADS_TAB);
  if (!sheet) { throw new Error('Sheet tab "' + LEADS_TAB + '" not found.'); }
  var values = sheet.getDataRange().getValues();
  if (values.length < 2) { return []; }
  var headers = values[0].map(function (h) { return String(h).trim(); });
  var rows = [];
  for (var i = 1; i < values.length; i++) {
    var row = {};
    for (var c = 0; c < headers.length; c++) { row[headers[c]] = values[i][c]; }
    rows.push(row);
  }
  return rows;
}

function num_(v) {
  var n = parseFloat(String(v == null ? '' : v).replace(/[$,]/g, '').trim());
  return isNaN(n) ? 0 : n;
}
function str_(v) { return String(v == null ? '' : v).trim(); }

/** Quarter key like "2024-Q1" plus a sortable index, from an entry_date. */
function quarterOf_(entryDate) {
  var d = (entryDate instanceof Date) ? entryDate : new Date(String(entryDate));
  if (isNaN(d.getTime())) { return null; }
  var y = d.getFullYear();
  var q = Math.floor(d.getMonth() / 3) + 1;
  return { key: y + '-Q' + q, sort: y * 4 + (q - 1), year: y };
}

/**
 * The single call the front-end makes. Returns a compact payload:
 *   kpis, quarterly[], categories[], actionable[] (capped), meta.
 */
function getDashboardData(pass) {
  if (String(pass) !== ACCESS_PASSWORD) { return { error: 'unauthorized' }; }
  var rows = readLeads_();

  var enrollment = 0, revenue = 0, messages = 0, needHuman = 0;
  var quarters = {};                // sortIndex -> {key, enroll, rev}
  var catCounts = {};               // category -> count (contact_us only)
  var actionable = [];

  for (var i = 0; i < rows.length; i++) {
    var r = rows[i];
    var source = str_(r.source).toLowerCase();

    if (source === 'registration') {
      enrollment += 1;
      var amt = num_(r.amount_usd);
      revenue += amt;
      var q = quarterOf_(r.entry_date);
      if (q) {
        if (!quarters[q.sort]) { quarters[q.sort] = { key: q.key, year: q.year, enroll: 0, rev: 0 }; }
        quarters[q.sort].enroll += 1;
        quarters[q.sort].rev += amt;
      }
    } else if (source === 'contact_us') {
      messages += 1;
      var cat = str_(r.category).toLowerCase() || 'general';
      var sent = str_(r.sentiment).toLowerCase();
      catCounts[cat] = (catCounts[cat] || 0) + 1;

      var isLead = LEAD_CATEGORIES.indexOf(cat) !== -1;
      var isUrgent = (sent === 'urgent');
      if (isLead || isUrgent) {
        needHuman += 1;
        actionable.push({
          date: formatDate_(r.entry_date),
          sortDate: dateSort_(r.entry_date),
          name: str_(r.name) || '(no name)',
          email: str_(r.email),
          category: cat,
          urgent: isUrgent,
          summary: str_(r.summary)
        });
      }
    }
  }

  // quarterly series, chronological, filling gaps so the line is continuous
  var idx = Object.keys(quarters).map(Number).sort(function (a, b) { return a - b; });
  var quarterly = [];
  if (idx.length) {
    for (var k = idx[0]; k <= idx[idx.length - 1]; k++) {
      var qd = quarters[k];
      quarterly.push(qd ? { key: qd.key, year: qd.year, enroll: qd.enroll, rev: Math.round(qd.rev) }
                        : { key: '', year: Math.floor(k / 4), enroll: 0, rev: 0 });
    }
  }

  // categories sorted desc
  var categories = Object.keys(catCounts)
    .map(function (c) { return { category: c, count: catCounts[c] }; })
    .sort(function (a, b) { return b.count - a.count; });

  // actionable: newest first, capped so the page stays fast
  actionable.sort(function (a, b) { return b.sortDate - a.sortDate; });
  var CAP = 300;
  var actionableCapped = actionable.slice(0, CAP);

  return {
    kpis: {
      enrollment: enrollment,
      revenue: Math.round(revenue),
      messages: messages,
      needHuman: needHuman
    },
    quarterly: quarterly,
    categories: categories,
    actionable: actionableCapped,
    meta: {
      totalActionable: actionable.length,
      capped: actionable.length > CAP,
      updated: Utilities.formatDate(new Date(), Session.getScriptTimeZone() || 'America/Los_Angeles', 'MMM d, yyyy h:mm a')
    }
  };
}

function formatDate_(v) {
  var d = (v instanceof Date) ? v : new Date(String(v));
  if (isNaN(d.getTime())) { return str_(v); }
  return Utilities.formatDate(d, Session.getScriptTimeZone() || 'America/Los_Angeles', 'MMM d, yyyy');
}
function dateSort_(v) {
  var d = (v instanceof Date) ? v : new Date(String(v));
  return isNaN(d.getTime()) ? 0 : d.getTime();
}
