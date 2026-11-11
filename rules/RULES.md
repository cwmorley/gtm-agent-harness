# Routing rules, in plain language

Source of truth: `routing_rules.json` (this folder). Current `rules_version`: **2026.11.2**.
All names, companies and owner IDs are fictional.

The harness reads these rules; it does not contain them. If a rule here and the JSON disagree,
the JSON wins and this file has a bug.

## Field definitions

| Field | Meaning | Where it comes from |
|---|---|---|
| `company_domain` | The company's web domain | Form website, else the email domain if it is not free-mail |
| `business_email` | True when the form email is not on a free-mail domain | Form |
| `work_email` | An email at the company's own domain | Form if business email, else enrichment |
| `employee_count` | Whole number of employees, greater than zero | Form, CRM or enrichment |
| `industry` | Lower-case label such as `software` | Form, CRM or enrichment |
| `hq_country` | Two-letter country code | Form, CRM or enrichment |
| `tech_stack` | List of tools the company uses | CRM or enrichment |
| `use_case`, `urgency`, `competitor` | Intent read from the free-text field | Model, after validation |

## Disqualifiers (checked before any money is spent, and again after enrichment)

1. `no_company_domain`: free-mail address and no website. We cannot tell who the company is.
2. `blocked_domain`: the domain is on the block list (rival agencies, internal tests), or is a
   subdomain of a listed domain.
3. `disqualified_industry`: gambling, adult, student project.

A disqualified lead is not enriched, not sent to the model, not scored. Grade: `DISQUALIFIED`.

## Score (0 to 100)

Fit, from company facts (70 points):

| Criterion | Rule | Points |
|---|---|---|
| `employee_count_in_range` | 50 to 5000 employees | 25 |
| `target_industry` | software, fintech, logistics, healthcare_tech, manufacturing | 20 |
| `uses_target_crm` | tech stack includes hubspot or salesforce | 15 |
| `business_email` | form email is not free-mail | 10 |

Intent, from the validated model output (30 points):

| Criterion | Rule | Points |
|---|---|---|
| `urgency_high` | urgency is `high` | 15 |
| `use_case_we_serve` | lead routing, CRM migration, data enrichment, reporting, AI agents | 10 |
| `competitor_mentioned` | a competitor is named in the text | 5 |

Intent is capped at 30 on purpose: free text alone can never reach grade B (50).
If the model output fails validation, intent scores zero and the lead goes to review.

## Grades, actions and SLA

| Grade | Score | Allowed actions | Owner | SLA |
|---|---|---|---|---|
| A | 75+ | `route_to_ae`, `create_task` | AE for the territory | 5 minutes |
| B | 50 to 74 | `route_to_sdr` | SDR for the territory | 60 minutes |
| C | 25 to 49 | `nurture` | none | 1 day |
| D | 0 to 24 | `status_only` (email and lead status, nothing else) | none | none |
| DISQUALIFIED | n/a | `status_only` | none | none |

## Owner assignment

1. If the person, or a colleague at the same company domain, is already in the CRM with an
   owner who is on the roster, that owner keeps the lead.
2. Otherwise: the roster member with the grade's role in the territory of `hq_country`.
3. Territories: NA (US, CA), EMEA (GB, DE, FR, NL), APAC (AU, SG). Unknown country: NA.

## Enrichment waterfall

Per field, providers are tried in the listed order; the first value that passes the field's
validation wins. A provider is charged when it returns a value (valid or not), never for a miss.

| Field | Provider order | Validation |
|---|---|---|
| `work_email` | alpha, beta, gamma | email syntax, and domain equals the company domain |
| `employee_count` | alpha, beta, gamma | whole number greater than zero |
| `industry` | alpha, beta | non-empty text |
| `hq_country` | alpha, beta, gamma | two capital letters |
| `tech_stack` | beta, gamma | non-empty list |

Run conditions: a field is enriched only if the form and the CRM both left it empty, and never
for a disqualified lead.

## CRM write rules

- A non-empty CRM value is never replaced unless the property is in `overwritable_properties`
  (employee count, tech stack, lead status, grade, score, intent fields, rules version, SLA, owner).
- `required_properties` lists what each action must write. A plan missing one goes to review.
