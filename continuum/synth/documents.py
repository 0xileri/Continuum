"""Synthetic unstructured documents for the Claude reasoning agent (§7 part 2).

§6 lists "Covenant/document data ... parsed by an LLM agent, not a human analyst" as a Layer 1
source. These are the documents that agent reads.

They are written to be genuinely non-trivial to classify: the "soft warning" texts contain
covenant *language* and worried tone but no actual breach, and the clean texts mention
covenants in passing. A keyword matcher should get these wrong; that gap is the point of
using a reasoning model, and it is what the offline fixture's accuracy is measured against.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DocTemplate:
    doc_type: str
    title: str
    body: str
    # Ground truth, used ONLY for evaluating the agent — never shown to it.
    truth_covenant_breach: bool = False
    truth_adverse_news: bool = False
    truth_payer_deterioration: bool = False


TEMPLATES: dict[str, DocTemplate] = {
    # ---- Clean: mentions covenants, breaches none ------------------------------------
    "clean_covenant": DocTemplate(
        "covenant_certificate",
        "Quarterly Covenant Compliance Certificate",
        """To: Facility Agent
Re: {borrower} — Compliance Certificate, quarter ended {period}

Pursuant to Clause 21.2 of the Receivables Finance Agreement dated 14 March 2025, the
undersigned certifies that as at the quarter end:

  (a) Minimum Interest Cover Ratio      required >= 2.00x   actual 3.14x
  (b) Maximum Net Leverage              required <= 3.50x   actual 1.92x
  (c) Minimum Tangible Net Worth        required >= GBP 1.20m  actual GBP 2.41m
  (d) Debtor Concentration Limit        required <= 45%     actual 34%

No Default or Event of Default (as defined in Clause 1.1) has occurred and is continuing.
The Borrower confirms all receivables assigned during the period were bona fide trade
receivables arising in the ordinary course of business.

Signed, {borrower} — Finance Director""",
    ),
    "routine_statement": DocTemplate(
        "financial_statement",
        "Management Accounts — Monthly Summary",
        """{borrower} — Management Accounts, {period}

Revenue for the period was GBP {revenue:,.0f}, in line with budget. Gross margin held at
32.4% (prior period 32.1%). Trade receivables stood at GBP {receivables:,.0f} with debtor
days of {dso:.0f}, broadly consistent with the trailing twelve-month average.

Cash at bank was GBP {cash:,.0f}. The facility remains undrawn beyond the assigned
receivables pool. No material post-balance-sheet events have arisen. The directors note the
usual seasonal softness expected in the coming quarter and consider existing headroom
adequate.""",
    ),
    # ---- Soft warning: worrying tone, NO actual breach --------------------------------
    "soft_covenant_warning": DocTemplate(
        "covenant_certificate",
        "Quarterly Covenant Compliance Certificate (with commentary)",
        """To: Facility Agent
Re: {borrower} — Compliance Certificate, quarter ended {period}

The undersigned certifies compliance with all financial covenants under Clause 21.2:

  (a) Minimum Interest Cover Ratio      required >= 2.00x   actual 2.16x
  (b) Maximum Net Leverage              required <= 3.50x   actual 3.31x
  (c) Debtor Concentration Limit        required <= 45%     actual 43%

Management commentary: headroom under (a) and (b) has narrowed materially versus the prior
quarter (2.74x and 2.88x respectively). Should the current trading trajectory persist without
mitigation, the Borrower anticipates that headroom under the Interest Cover Ratio may become
constrained in the next testing period. Management has initiated a cost review and is in
discussions regarding an extension of supplier terms.

No Event of Default has occurred and is continuing as at the date of this certificate.

Signed, {borrower} — Finance Director""",
    ),
    "margin_pressure_statement": DocTemplate(
        "financial_statement",
        "Management Accounts — Monthly Summary",
        """{borrower} — Management Accounts, {period}

Revenue for the period was GBP {revenue:,.0f}, below budget by 11.2%. Gross margin compressed
to 24.8% (prior period 29.6%) driven by input cost inflation the Borrower has been unable to
pass through under existing fixed-price customer contracts.

Trade receivables were GBP {receivables:,.0f}, with debtor days extending to {dso:.0f} from
an average of {dso_prior:.0f} over the trailing twelve months. The increase is attributed
principally to slower settlement by the Borrower's largest customer.

Cash at bank was GBP {cash:,.0f}. The directors are monitoring working capital closely and
have deferred discretionary capital expenditure. Trading remains within the parameters of the
existing facility.""",
    ),
    # ---- Genuine breach ---------------------------------------------------------------
    "covenant_breach_notice": DocTemplate(
        "breach_notice",
        "Notice of Covenant Breach",
        """To: Facility Agent
From: {borrower}
Re: Notification pursuant to Clause 22.1 (Information: Miscellaneous)

The Borrower hereby gives notice, as required under Clause 22.1(c) of the Receivables Finance
Agreement, that as at the testing date {period} the following financial covenant was NOT
satisfied:

  Minimum Interest Cover Ratio          required >= 2.00x   actual 1.42x

This constitutes an Event of Default under Clause 24.2 (Financial Covenants). The breach
arises from a combination of revenue shortfall against forecast and increased finance costs
during the period.

The Borrower additionally notifies that the Debtor Concentration Limit under Clause 21.2(d)
was exceeded, standing at 61% against a permitted maximum of 45%, following the loss of two
secondary customer accounts.

The Borrower requests a waiver and proposes to present a remediation plan within 21 days.""",
        truth_covenant_breach=True,
    ),
    # ---- Payer-side deterioration (§13 payer risk) -------------------------------------
    "dispute_correspondence": DocTemplate(
        "dispute_correspondence",
        "Customer Correspondence — Disputed Invoices",
        """From: Accounts Payable, {payer_name}
To: Credit Control, {borrower}
Subject: RE: RE: Overdue account — invoices {inv_a}, {inv_b}, {inv_c}

Further to your reminders, we are formally disputing the above invoices totalling
GBP {disputed_amount:,.0f} pending resolution of the quality issues raised in our note of last
month. Our position is that the goods delivered did not conform to specification and we are
withholding settlement of the full balance until this is resolved.

We should also be transparent that our own payment run has been rescheduled this quarter. Our
group treasury has moved us to a 90-day cycle across all suppliers with immediate effect, and
we are not in a position to make exceptions. We are aware this represents a change from the
30-day terms in our supply agreement.

We will revert once the quality review concludes.""",
        truth_payer_deterioration=True,
    ),
    "adverse_news": DocTemplate(
        "news_article",
        "Trade Press — Sector Coverage",
        """{sector_title} Weekly — {period}

**{borrower} weighs restructuring as lender talks continue**

{borrower} has appointed advisers to review its capital structure, according to three people
familiar with the matter. The company is understood to have entered discussions with its
principal receivables financier after a period of deteriorating trading.

The review follows the loss of a significant customer contract earlier this year and what one
person described as "a sustained squeeze on working capital". Filings show the company's most
recent accounts were submitted after the statutory deadline.

A spokesperson for {borrower} said the company "keeps its financing arrangements under regular
review" and declined to comment on speculation. Separately, credit insurers are understood to
have reduced cover on the company in recent weeks — often an early signal of distress in the
sector.""",
        truth_adverse_news=True,
    ),
    "recovery_statement": DocTemplate(
        "financial_statement",
        "Management Accounts — Monthly Summary",
        """{borrower} — Management Accounts, {period}

Revenue for the period was GBP {revenue:,.0f}, ahead of budget by 7.4% and the fourth
consecutive month of growth. Gross margin recovered to 31.2% (prior period 27.9%) following
completion of the contract repricing programme.

Trade receivables were GBP {receivables:,.0f}. Debtor days improved to {dso:.0f} from
{dso_prior:.0f}, reflecting tightened credit control and the exit of two persistently slow-
paying accounts.

Cash at bank was GBP {cash:,.0f}. The directors note that covenant headroom has been restored
to comfortable levels and that the remediation plan agreed with the facility agent is now
complete. No Event of Default subsists.""",
    ),
}


PROVENANCE: dict[str, str] = {
    "covenant_certificate": "self_reported",
    "financial_statement": "self_reported",
    "breach_notice": "self_reported",
    "dispute_correspondence": "third_party",
    "news_article": "third_party",
}
"""Where each document kind comes from, carried through to the agent prompt.

§13's first mitigation is to prefer direct integrations over borrower-supplied documents and to
treat "single-source, unverifiable data as lower-confidence input". The agent cannot apply that
rule unless it is told which is which, so provenance travels with the document rather than being
inferred from tone. A compliance certificate the borrower signed and a payer's own dispute letter
are not equally good evidence about the borrower, even when they say the same thing.

Note that a self-reported document can still be strong evidence when it is adverse to the
borrower's own interest — a breach notice is self-reported but nobody files one for fun. That
judgement is the agent's to make; provenance is the input to it, not a fixed discount.
"""


SECTOR_TITLES = {
    "manufacturing": "Industrial Manufacturing",
    "food_beverage": "Food & Beverage",
    "healthcare": "Healthcare Supply",
    "apparel": "Apparel & Textiles",
    "construction": "Construction & Infrastructure",
    "logistics": "Transport & Logistics",
    "industrial": "Industrial Distribution",
    "energy": "Energy & Renewables",
}
