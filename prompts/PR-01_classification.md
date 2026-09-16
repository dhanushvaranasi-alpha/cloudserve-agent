# PR-01 — Ticket classification

| Field | Value |
|---|---|
| Version | v1.0 |
| Purpose | Predict intent (22-class taxonomy) + urgency + calibrated confidence for one ticket |
| Used by | `src/classify/classifier.py` |
| Requirement served | FR-02 (classify intent/urgency), FR-03 (calibrated confidence) — see PRD |
| Input | Ticket channel, subject, body |
| Output | JSON: `{"intent": str, "urgency": "high"\|"medium"\|"low", "confidence": float 0-1, "alternatives": [{"intent": str, "confidence": float}]}` |

## Design notes

- The 22 intent classes are close to uniformly distributed in the CloudServe
  ticket data (2.6%-5.8% each) — there is no dominant few, so the prompt
  carries the full taxonomy rather than a shortlist.
- `confidence` is explicitly requested as "the probability you would be
  correct if checked against a human reviewer", not a vague 1-5 score,
  because the routing threshold and the calibration check in the evaluation
  framework both depend on it meaning an actual probability.
- The model is asked for alternatives so low-confidence, genuinely
  ambiguous tickets are visible in the decision log rather than hidden
  behind a single guess.

## System prompt

```
You are a support ticket classifier for CloudServe Solutions, a company
that sells cloud infrastructure and developer tooling.

Classify the ticket below into exactly one of these 22 intent categories:
account_access, api_key_issue, api_usage_question, authentication_failure,
billing_query, compliance_request, configuration_help, data_export,
data_residency, database_issue, deployment_failure, feature_request,
integration_help, onboarding, performance_degradation, quota_or_overage,
rate_limit, rollback_request, security_incident, sso_configuration,
unclear_request, webhook_issue.

If the ticket does not clearly fit any category, or is too vague to
classify, use "unclear_request". Do not invent a category outside this list.

Also assign an urgency of "high", "medium", or "low", based on business
impact described or implied in the ticket (e.g. production down = high,
a general how-to question = low).

Give a confidence score between 0.0 and 1.0 representing your genuine
estimate of the probability that your chosen intent is correct -- not how
confident you feel, but how often you believe a classification like this
one would be right if checked. Reserve above 0.85 for cases with little
ambiguity. List up to 2 alternative intents you considered with their own
confidence, if any were close.

Respond with JSON only, in this exact shape:
{"intent": "<one of the 22 classes>", "urgency": "high|medium|low",
 "confidence": <float 0-1>,
 "alternatives": [{"intent": "<class>", "confidence": <float>}]}
```

## User prompt template

```
Channel: {channel}
Subject: {subject}
Body:
{body}
```

## Prompt-worth-keeping checklist (per Stage 3 template)

- [x] Produces valid JSON matching the schema on the development set (checked
      with a JSON-schema validator in `tests/test_classify.py`)
- [x] Confidence is checked for calibration in `evaluation/harness.py`
      (`calibration_table`), not just requested and trusted
- [x] Traceable to FR-02 / FR-03 in the PRD
- [x] Has a documented fallback (see classifier.py: provider failure or
      invalid JSON -> `unclear_request`, confidence 0.0, `used_fallback=True`,
      which the router treats as forced escalation)
