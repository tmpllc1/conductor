// ============================================================
// Contract test JS mirror validator — INF-106
// Ports the stage2 output validators from
//   n8n-workflows/vf-dealswarm-v2/nodes/node-06.js
// into a standalone stdin->stdout tool so Python pytest can call it
// via `docker exec -i n8n-n8n-1 node < js_validator.js`.
//
// IMPORTANT: Do NOT drift from node-06.js. When node-06.js changes,
// mirror the change here and regenerate contract fixtures.
// ============================================================
'use strict';

var VALID_RISK_RATINGS = ['Low', 'Low-Moderate', 'Medium', 'Moderate-Elevated', 'High', 'Very High'];
var VALID_PROPERTY_TYPES = ['residential', 'commercial', 'mixed_use', 'land', 'multi_family', 'industrial', 'other'];
var VALID_LENDER_FITS = ['strong', 'moderate', 'weak'];
var VALID_COMPLIANCE_STATUSES = ['clear', 'warning', 'fail'];

function isNum(x) { return typeof x === 'number' && !isNaN(x); }

function validateExtractor(d) {
  var errors = [];
  if (!d || typeof d !== 'object') { return { valid: false, errors: ['extractor output is not an object'] }; }
  var b = d.borrower || {};
  if (!b.name) { errors.push('borrower.name missing'); }
  var p = d.property || {};
  if (!p.address) { errors.push('property.address missing'); }
  if (p.property_type && VALID_PROPERTY_TYPES.indexOf(p.property_type) === -1) {
    errors.push('property.property_type invalid: ' + p.property_type);
  }
  var ln = d.loan || {};
  if (ln.requested_amount === undefined || ln.requested_amount === null) {
    errors.push('loan.requested_amount missing');
  }
  // LTV must be a top-level scalar. Contract tightening beyond node-06.js:
  // node-06 silently accepts a missing ltv; the contract is stricter and
  // requires the top-level scalar to be present and numeric.
  if (d.ltv === undefined || d.ltv === null) {
    errors.push('ltv missing at top level (must be scalar float)');
  } else if (!isNum(d.ltv) || d.ltv < 0 || d.ltv > 200) {
    errors.push('ltv out of range [0,200] or non-numeric: ' + JSON.stringify(d.ltv));
  }
  // Detect the specific nesting bug: ltv under calculated_fields
  if (d.calculated_fields && d.calculated_fields.ltv !== undefined) {
    errors.push('ltv must not be nested under calculated_fields');
  }
  return { valid: errors.length === 0, errors: errors };
}

function validateRisk(d) {
  var errors = [];
  if (!d || typeof d !== 'object') { return { valid: false, errors: ['risk output is not an object'] }; }
  if (VALID_RISK_RATINGS.indexOf(d.overall_rating) === -1) {
    errors.push('overall_rating not a valid 6-level rating: ' + d.overall_rating);
  }
  // node-06 currently expects score 1.0-5.0, but the Pydantic schema uses 0-100.
  // Mirror node-06 as-is; the contract spot-check will highlight any drift.
  if (!isNum(d.overall_score) || d.overall_score < 1.0 || d.overall_score > 5.0) {
    errors.push('overall_score must be 1.0-5.0 (got ' + d.overall_score + ')');
  }
  var rfs = d.risk_factors || [];
  if (!Array.isArray(rfs) || rfs.length === 0) {
    errors.push('risk_factors must be non-empty array');
  }
  var subRisks = ['ltv_risk', 'credit_risk', 'exit_strategy_risk', 'income_risk', 'property_risk'];
  for (var i = 0; i < subRisks.length; i++) {
    var key = subRisks[i];
    if (VALID_RISK_RATINGS.indexOf(d[key]) === -1) {
      errors.push(key + ' not a valid 6-level rating: ' + d[key]);
    }
  }
  return { valid: errors.length === 0, errors: errors };
}

function validateLender(d) {
  var errors = [];
  if (!d || typeof d !== 'object') { return { valid: false, errors: ['lender output is not an object'] }; }
  var recs = d.recommended_lenders || [];
  if (!Array.isArray(recs) || recs.length === 0) {
    errors.push('recommended_lenders must be non-empty');
  } else {
    for (var i = 0; i < recs.length; i++) {
      var r = recs[i];
      if (!r.lender_name) { errors.push('recommended_lenders[' + i + '].lender_name missing'); }
      // Tightened: the *field name* must be `fit`, not `fit_score`.
      if (r.fit === undefined) {
        errors.push('recommended_lenders[' + i + '].fit missing (field must be named "fit", not "fit_score")');
      } else if (VALID_LENDER_FITS.indexOf(r.fit) === -1) {
        errors.push('recommended_lenders[' + i + '].fit invalid: ' + r.fit);
      }
      if (r.fit_score !== undefined) {
        errors.push('recommended_lenders[' + i + '].fit_score must not be present; use "fit" instead');
      }
      // Tightened: must be `match_reasons`, not `rationale`.
      var mr = r.match_reasons;
      if (mr === undefined) {
        errors.push('recommended_lenders[' + i + '].match_reasons missing (field must be named "match_reasons", not "rationale")');
      } else if (!Array.isArray(mr) || mr.length === 0) {
        errors.push('recommended_lenders[' + i + '].match_reasons must be non-empty');
      }
      if (r.rationale !== undefined) {
        errors.push('recommended_lenders[' + i + '].rationale must not be present; use "match_reasons" instead');
      }
    }
  }
  return { valid: errors.length === 0, errors: errors };
}

function validateCompliance(d) {
  var errors = [];
  if (!d || typeof d !== 'object') { return { valid: false, errors: ['compliance output is not an object'] }; }
  if (VALID_COMPLIANCE_STATUSES.indexOf(d.overall_status) === -1) {
    errors.push('overall_status not in clear|warning|fail: ' + d.overall_status);
  }
  var checks = d.checks || [];
  if (!Array.isArray(checks) || checks.length === 0) {
    errors.push('checks must be non-empty');
  } else {
    var hasS347 = false, hasBCFSA = false, hasFINTRAC = false;
    for (var i = 0; i < checks.length; i++) {
      var c = checks[i];
      if (VALID_COMPLIANCE_STATUSES.indexOf(c.status) === -1) {
        errors.push('checks[' + i + '].status invalid: ' + c.status);
      }
      var nm = (c.check_name || '').toLowerCase();
      if (nm.indexOf('s.347') !== -1 || nm.indexOf('s347') !== -1) { hasS347 = true; }
      if (nm.indexOf('bcfsa') !== -1) { hasBCFSA = true; }
      if (nm.indexOf('fintrac') !== -1) { hasFINTRAC = true; }
    }
    if (!hasS347) { errors.push('missing mandatory s.347 check'); }
    if (!hasBCFSA) { errors.push('missing mandatory BCFSA check'); }
    if (!hasFINTRAC) { errors.push('missing mandatory FINTRAC check'); }
    if (d.overall_status === 'fail') {
      var hasAction = false;
      for (var j = 0; j < checks.length; j++) { if (checks[j].action_required) { hasAction = true; break; } }
      if (!hasAction) { errors.push('overall_status=fail but no check has action_required'); }
    }
  }
  return { valid: errors.length === 0, errors: errors };
}

var VALIDATORS = {
  extractor: validateExtractor,
  risk: validateRisk,
  lender: validateLender,
  compliance: validateCompliance,
};

// stdin: {"schema": "extractor|risk|lender|compliance", "data": {...}}
// stdout: {"valid": bool, "errors": [...]}
var chunks = [];
process.stdin.on('data', function (c) { chunks.push(c); });
process.stdin.on('end', function () {
  var input;
  try {
    input = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } catch (e) {
    process.stdout.write(JSON.stringify({ valid: false, errors: ['invalid stdin JSON: ' + e.message] }));
    process.exit(0);
  }
  var schema = input.schema;
  var data = input.data;
  var fn = VALIDATORS[schema];
  if (!fn) {
    process.stdout.write(JSON.stringify({ valid: false, errors: ['unknown schema: ' + schema] }));
    process.exit(0);
  }
  var result = fn(data);
  process.stdout.write(JSON.stringify(result));
});
