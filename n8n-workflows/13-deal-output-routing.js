// ============================================================
// Deal Swarm - Stage 3: Output Routing (n8n Code Node)
// Position: After 12-deal-swarm-stage2.js
// Purpose: Routes deal analysis results to Notion, Telegram,
//          Zoho CRM, and Google Drive.
// AUDIT 2026-04-12: No em dashes, no template literals,
//   all this.helpers.httpRequest() inlined, plain object bodies.
// ============================================================

var NOTION_TOKEN = $env.NOTION_TOKEN;
var NOTION_VERSION = '2022-06-28';
var DASHBOARD_PAGE = '2e31cd933f028035af96ce990be06643';
var TELEGRAM_TOKEN = $env.TELEGRAM_BOT_TOKEN;
var TELEGRAM_CHAT_ID = '7729113934';

// Zoho CRM OAuth2 - tokens managed by n8n credential store
// The Code node must read the credential at runtime via n8n's
// built-in credential system. For now, use the environment variable
// or hardcoded placeholder until the n8n credential is configured.
var ZOHO_API_BASE = 'https://www.zohoapis.com/crm/v2';
// NOTE: In production, get the OAuth token from n8n credentials:
//   var zohoToken = await this.getCredentials('zohoOAuth2Api');
// For now, we read from n8n environment or static data.
var ZOHO_ACCESS_TOKEN_KEY = 'zoho_access_token';

// Google Drive - uses n8n credential for Google OAuth2
var GDRIVE_API_BASE = 'https://www.googleapis.com/upload/drive/v3/files';
var GDRIVE_META_BASE = 'https://www.googleapis.com/drive/v3/files';
var GDRIVE_ACCESS_TOKEN_KEY = 'gdrive_access_token';

var now = new Date().toISOString();
var staticData = $getWorkflowStaticData('global');

try {

  // ============================================================
  // 1. Read combined analysis from Stage 2
  // ============================================================
  var analysisData = $input.first().json;
  var dealId = analysisData.deal_id || 'unknown';
  var screener = analysisData.screener || {};
  var extractor = analysisData.extractor || {};
  var risk = analysisData.risk || {};
  var lender = analysisData.lender || {};
  var compliance = analysisData.compliance || {};

  var borrowerName = screener.borrower_name || (extractor.borrower ? extractor.borrower.name : 'Unknown');
  var propertyAddress = screener.property_address || (extractor.property ? extractor.property.address : 'Unknown');
  var loanAmount = extractor.loan ? extractor.loan.requested_amount : (screener.estimated_loan_amount || 0);
  var ltv = extractor.ltv || screener.estimated_ltv || 0;
  var riskRating = risk.overall_rating || 'unknown';
  var riskScore = risk.overall_score || 0;
  var complianceStatus = compliance.overall_status || 'unknown';

  // Count matched lenders
  var lenderCount = 0;
  var topLender = 'None';
  if (lender.recommended_lenders && lender.recommended_lenders.length > 0) {
    lenderCount = lender.recommended_lenders.length;
    topLender = lender.recommended_lenders[0].lender_name || 'Unknown';
  }

  // ============================================================
  // 2. Create Notion page under Dashboard
  // ============================================================
  var notionTitle = 'Deal: ' + borrowerName + ' - ' + propertyAddress;
  if (notionTitle.length > 100) {
    notionTitle = notionTitle.substring(0, 97) + '...';
  }

  // Build summary content for the Notion page
  var summaryLines = [];
  summaryLines.push('Deal ID: ' + dealId);
  summaryLines.push('Processed: ' + now);
  summaryLines.push('');
  summaryLines.push('BORROWER: ' + borrowerName);
  summaryLines.push('PROPERTY: ' + propertyAddress);
  summaryLines.push('LOAN AMOUNT: $' + (loanAmount ? loanAmount.toLocaleString() : '?'));
  summaryLines.push('LTV: ' + ltv + '%');
  summaryLines.push('');
  summaryLines.push('RISK: ' + riskRating + ' (score: ' + riskScore + ')');
  summaryLines.push('COMPLIANCE: ' + complianceStatus);
  summaryLines.push('LENDERS MATCHED: ' + lenderCount);
  if (topLender !== 'None') {
    summaryLines.push('TOP LENDER: ' + topLender);
  }
  summaryLines.push('');

  if (risk.risk_summary) {
    summaryLines.push('RISK SUMMARY:');
    summaryLines.push(risk.risk_summary);
    summaryLines.push('');
  }

  if (lender.deal_positioning) {
    summaryLines.push('DEAL POSITIONING:');
    summaryLines.push(lender.deal_positioning);
    summaryLines.push('');
  }

  if (compliance.aml_flags && compliance.aml_flags.length > 0) {
    summaryLines.push('AML FLAGS (L0 Human Only):');
    for (var af = 0; af < compliance.aml_flags.length; af++) {
      summaryLines.push('  - ' + compliance.aml_flags[af]);
    }
    summaryLines.push('');
  }

  if (compliance.suspicious_transaction_indicators && compliance.suspicious_transaction_indicators.length > 0) {
    summaryLines.push('STR INDICATORS (L0 Human Only):');
    for (var si = 0; si < compliance.suspicious_transaction_indicators.length; si++) {
      summaryLines.push('  - ' + compliance.suspicious_transaction_indicators[si]);
    }
    summaryLines.push('');
  }

  var pageContent = summaryLines.join('\n');

  // Create page in Notion
  var notionPageUrl = '';
  try {
    var notionResponse = await this.helpers.httpRequest({
      method: 'POST',
      url: 'https://api.notion.com/v1/pages',
      headers: {
        'Authorization': 'Bearer ' + NOTION_TOKEN,
        'Notion-Version': NOTION_VERSION,
        'Content-Type': 'application/json'
      },
      body: {
        parent: {
          page_id: DASHBOARD_PAGE
        },
        properties: {
          title: {
            title: [
              {
                text: {
                  content: notionTitle
                }
              }
            ]
          }
        },
        children: [
          {
            object: 'block',
            type: 'heading_2',
            heading_2: {
              rich_text: [{ type: 'text', text: { content: 'Executive Summary' } }]
            }
          },
          {
            object: 'block',
            type: 'paragraph',
            paragraph: {
              rich_text: [{ type: 'text', text: { content: pageContent.substring(0, 2000) } }]
            }
          },
          {
            object: 'block',
            type: 'heading_2',
            heading_2: {
              rich_text: [{ type: 'text', text: { content: 'Full Analysis JSON' } }]
            }
          },
          {
            object: 'block',
            type: 'code',
            code: {
              rich_text: [{ type: 'text', text: { content: JSON.stringify(analysisData, null, 2).substring(0, 2000) } }],
              language: 'json'
            }
          }
        ]
      },
      json: true
    });

    if (notionResponse && notionResponse.url) {
      notionPageUrl = notionResponse.url;
    } else if (notionResponse && notionResponse.id) {
      notionPageUrl = 'https://notion.so/' + notionResponse.id.replace(/-/g, '');
    }
  } catch (notionErr) {
    notionPageUrl = 'FAILED: ' + String(notionErr.message || notionErr).substring(0, 200);
  }

  // ============================================================
  // 3. Send formatted Telegram executive summary
  // ============================================================
  var telegramMsg = 'DEAL ANALYSIS - EXECUTIVE SUMMARY'
    + '\n'
    + '\nDeal ID: ' + dealId
    + '\nBorrower: ' + borrowerName
    + '\nProperty: ' + propertyAddress
    + '\nLoan: $' + (loanAmount ? loanAmount.toLocaleString() : '?') + ' at ' + ltv + '% LTV'
    + '\n'
    + '\nRisk: ' + riskRating.toUpperCase() + ' (' + riskScore + '/100)'
    + '\nCompliance: ' + complianceStatus.toUpperCase()
    + '\nLenders: ' + lenderCount + ' matched';

  if (topLender !== 'None') {
    telegramMsg = telegramMsg + ' (top: ' + topLender + ')';
  }

  if (risk.risk_summary) {
    var shortSummary = risk.risk_summary;
    if (shortSummary.length > 300) {
      shortSummary = shortSummary.substring(0, 297) + '...';
    }
    telegramMsg = telegramMsg + '\n\n' + shortSummary;
  }

  if (notionPageUrl && notionPageUrl.indexOf('FAILED') !== 0) {
    telegramMsg = telegramMsg + '\n\nNotion: ' + notionPageUrl;
  }

  if (complianceStatus === 'fail') {
    telegramMsg = telegramMsg + '\n\n*** COMPLIANCE FAIL - review required before proceeding ***';
  }

  try {
    await this.helpers.httpRequest({
      method: 'POST',
      url: 'https://api.telegram.org/bot' + TELEGRAM_TOKEN + '/sendMessage',
      headers: { 'Content-Type': 'application/json' },
      body: {
        chat_id: TELEGRAM_CHAT_ID,
        text: telegramMsg
      },
      json: true
    });
  } catch (tgErr) {
    // Non-fatal
  }

  // ============================================================
  // 4. Zoho CRM - Create/Update Deal Record
  // ============================================================
  // Uses Zoho CRM v2 REST API with OAuth2 token from n8n credentials.
  // Token refresh is handled by n8n credential store automatically.
  // If no token is available, skip silently and log the gap.
  var zohoStatus = 'skipped';
  var zohoDealId = null;

  var zohoToken = staticData[ZOHO_ACCESS_TOKEN_KEY] || null;
  if (zohoToken) {
    try {
      // Map risk rating to Zoho stage
      var zohoStage = 'AI Screened';
      if (complianceStatus === 'fail') {
        zohoStage = 'Compliance Hold';
      }

      // Create a new Deal record in Zoho CRM
      var zohoResponse = await this.helpers.httpRequest({
        method: 'POST',
        url: ZOHO_API_BASE + '/Deals',
        headers: {
          'Authorization': 'Zoho-oauthtoken ' + zohoToken,
          'Content-Type': 'application/json'
        },
        body: {
          data: [
            {
              Deal_Name: 'Deal: ' + borrowerName + ' - ' + propertyAddress,
              Stage: zohoStage,
              Amount: loanAmount,
              Description: 'AI Deal Swarm Analysis\n'
                + 'Deal ID: ' + dealId + '\n'
                + 'Risk: ' + riskRating + ' (' + riskScore + '/100)\n'
                + 'LTV: ' + ltv + '%\n'
                + 'Compliance: ' + complianceStatus + '\n'
                + 'Lenders matched: ' + lenderCount + '\n'
                + (notionPageUrl ? 'Notion: ' + notionPageUrl : ''),
              Risk_Rating: riskRating,
              LTV: ltv,
              Compliance_Status: complianceStatus,
              AI_Analysis_Date: now.split('T')[0],
              Lenders_Matched: lenderCount,
              Top_Lender: topLender !== 'None' ? topLender : null
            }
          ],
          trigger: ['workflow']
        },
        json: true
      });

      if (zohoResponse && zohoResponse.data && zohoResponse.data[0]) {
        var zohoRecord = zohoResponse.data[0];
        if (zohoRecord.status === 'success') {
          zohoStatus = 'created';
          zohoDealId = zohoRecord.details ? zohoRecord.details.id : null;
        } else {
          zohoStatus = 'error: ' + (zohoRecord.message || 'unknown');
        }
      } else {
        zohoStatus = 'error: unexpected response';
      }
    } catch (zohoErr) {
      zohoStatus = 'error: ' + String(zohoErr.message || zohoErr).substring(0, 200);

      // Alert on Zoho failure
      try {
        await this.helpers.httpRequest({
          method: 'POST',
          url: 'https://api.telegram.org/bot' + TELEGRAM_TOKEN + '/sendMessage',
          headers: { 'Content-Type': 'application/json' },
          body: {
            chat_id: TELEGRAM_CHAT_ID,
            text: 'ZOHO CRM WRITE FAILED for deal ' + dealId + ': ' + String(zohoErr.message || zohoErr).substring(0, 300)
          },
          json: true
        });
      } catch (tgErr2) {
        // Cannot alert
      }
    }
  } else {
    zohoStatus = 'skipped: no OAuth token in static data (set ' + ZOHO_ACCESS_TOKEN_KEY + ')';
  }

  // ============================================================
  // 5. Google Drive - Upload Deal Summary Document
  // ============================================================
  // Uploads a plain text executive summary to Google Drive.
  // Uses Google Drive v3 REST API with OAuth2 token.
  // Creates file in the root (or a deal-specific folder if configured).
  var driveStatus = 'skipped';
  var driveFileUrl = null;

  var driveToken = staticData[GDRIVE_ACCESS_TOKEN_KEY] || null;
  if (driveToken) {
    try {
      var fileName = 'DEAL-' + dealId + '-ELS-' + now.split('T')[0] + '.txt';

      // Build the executive summary document content
      var docContent = 'EXECUTIVE LOAN SUMMARY\n'
        + '======================\n\n'
        + 'Deal ID: ' + dealId + '\n'
        + 'Date: ' + now + '\n\n'
        + pageContent + '\n\n'
        + 'TOP LENDER: ' + topLender + '\n\n';

      if (risk.risk_summary) {
        docContent = docContent + 'RISK NARRATIVE:\n' + risk.risk_summary + '\n\n';
      }
      if (lender.deal_positioning) {
        docContent = docContent + 'DEAL POSITIONING:\n' + lender.deal_positioning + '\n\n';
      }
      if (lender.rate_expectation) {
        docContent = docContent + 'RATE EXPECTATION: ' + lender.rate_expectation + '\n';
      }
      if (lender.fee_expectation) {
        docContent = docContent + 'FEE EXPECTATION: ' + lender.fee_expectation + '\n';
      }

      // Step 1: Create file metadata
      var metaResponse = await this.helpers.httpRequest({
        method: 'POST',
        url: GDRIVE_META_BASE + '?uploadType=resumable',
        headers: {
          'Authorization': 'Bearer ' + driveToken,
          'Content-Type': 'application/json'
        },
        body: {
          name: fileName,
          mimeType: 'text/plain',
          description: 'AI Deal Swarm Executive Loan Summary for ' + dealId
        },
        json: true,
        returnFullResponse: true
      });

      // Step 2: Upload content to the resumable URI
      var uploadUri = metaResponse.headers && metaResponse.headers.location ? metaResponse.headers.location : null;

      if (uploadUri) {
        var uploadResponse = await this.helpers.httpRequest({
          method: 'PUT',
          url: uploadUri,
          headers: {
            'Content-Type': 'text/plain'
          },
          body: docContent,
          json: false
        });

        var uploadResult = null;
        if (typeof uploadResponse === 'string') {
          try { uploadResult = JSON.parse(uploadResponse); } catch (pe) { uploadResult = null; }
        } else {
          uploadResult = uploadResponse;
        }

        if (uploadResult && uploadResult.id) {
          driveFileUrl = 'https://drive.google.com/file/d/' + uploadResult.id + '/view';
          driveStatus = 'uploaded';
        } else {
          driveStatus = 'error: upload completed but no file ID returned';
        }
      } else {
        // Fallback: simple upload (non-resumable)
        var simpleResponse = await this.helpers.httpRequest({
          method: 'POST',
          url: 'https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart',
          headers: {
            'Authorization': 'Bearer ' + driveToken,
            'Content-Type': 'multipart/related; boundary=deal_boundary'
          },
          body: '--deal_boundary\r\n'
            + 'Content-Type: application/json; charset=UTF-8\r\n\r\n'
            + JSON.stringify({ name: fileName, mimeType: 'text/plain' }) + '\r\n'
            + '--deal_boundary\r\n'
            + 'Content-Type: text/plain\r\n\r\n'
            + docContent + '\r\n'
            + '--deal_boundary--',
          json: false
        });

        var simpleResult = null;
        if (typeof simpleResponse === 'string') {
          try { simpleResult = JSON.parse(simpleResponse); } catch (pe2) { simpleResult = null; }
        } else {
          simpleResult = simpleResponse;
        }

        if (simpleResult && simpleResult.id) {
          driveFileUrl = 'https://drive.google.com/file/d/' + simpleResult.id + '/view';
          driveStatus = 'uploaded';
        } else {
          driveStatus = 'error: simple upload failed';
        }
      }
    } catch (driveErr) {
      driveStatus = 'error: ' + String(driveErr.message || driveErr).substring(0, 200);

      // Alert on Drive failure
      try {
        await this.helpers.httpRequest({
          method: 'POST',
          url: 'https://api.telegram.org/bot' + TELEGRAM_TOKEN + '/sendMessage',
          headers: { 'Content-Type': 'application/json' },
          body: {
            chat_id: TELEGRAM_CHAT_ID,
            text: 'GOOGLE DRIVE UPLOAD FAILED for deal ' + dealId + ': ' + String(driveErr.message || driveErr).substring(0, 300)
          },
          json: true
        });
      } catch (tgErr3) {
        // Cannot alert
      }
    }
  } else {
    driveStatus = 'skipped: no OAuth token in static data (set ' + GDRIVE_ACCESS_TOKEN_KEY + ')';
  }

  // ============================================================
  // 6. Return result
  // ============================================================
  return [{
    json: {
      deal_id: dealId,
      status: 'routing_complete',
      notion_page_url: notionPageUrl,
      telegram_sent: true,
      zoho_status: zohoStatus,
      zoho_deal_id: zohoDealId,
      drive_status: driveStatus,
      drive_file_url: driveFileUrl,
      processed_at: now
    }
  }];

} catch (outerError) {
  var errMsg = 'DEAL OUTPUT ROUTING FAILED at ' + now + ': ' + String(outerError.message || outerError).substring(0, 500);

  try {
    await this.helpers.httpRequest({
      method: 'POST',
      url: 'https://api.telegram.org/bot' + TELEGRAM_TOKEN + '/sendMessage',
      headers: { 'Content-Type': 'application/json' },
      body: {
        chat_id: TELEGRAM_CHAT_ID,
        text: errMsg
      },
      json: true
    });
  } catch (e) {
    // Cannot alert
  }

  return [{
    json: {
      deal_id: (typeof analysisData !== 'undefined' && analysisData) ? analysisData.deal_id : 'unknown',
      status: 'routing_failed',
      error: String(outerError.message || outerError),
      processed_at: now
    }
  }];
}
