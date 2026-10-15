# Client feedback checklist (demo feedback received 15/10/2026)

Source: the client's Word document "Demo feedback" (sections 1 to 9), her covering message, and the
StableScore AI screenshots she shared as a presentation reference (not to be copied). Every request
in them is listed here so none is missed. Section numbers (§) point to her document.

Status key: `[x]` done and tested · `[~]` partly there (what exists is noted) · `[ ]` not started ·
`[?]` needs the client's answer first.

**Her one-sentence brief:** explain the business in everyday language, recommend practical actions,
follow up, measure the outcome and learn from it, as the core of Vyterlix, for an SME owner who has no
data analyst. Make it personalised, practical and easy to understand.

---

## 0. To confirm with the client

- [?] **Charts and graphs over words.** Raised verbally; the written feedback says words on top of
  charts ("beyond the dashboards, charts and figures…"). Agree the rule: a chart first, one plain
  sentence under it. Ask which screens most need charts (suggested: alerts and Today).
- [?] **Business types to support first** (suggest retail plus one service type, such as salon or barber).
- [?] **Is service-business data (bookings, appointments) in the original scope?** It changes the data
  model; check the contract and milestones.
- [?] **Her question list (§8)**: send her honest answers (see section 8 below) and what is planned.

---

## 1. The main purpose (§1)

- [ ] Vyterlix feels like a business analyst and practical adviser, not a set of charts and formulas.
- [~] **The first screen (Today) answers five questions:**
  - [~] What is going well? (health and figure changes show; no "going well" list yet)
  - [~] What needs my attention today? (attention items exist)
  - [ ] What should I do first? (one clear first step)
  - [ ] What am I waiting for? (actions in progress, results being checked)
  - [ ] Did my previous actions help? (a summary of results)
- [~] Detailed numbers stay available under "See details" or "How was this calculated?" (Key figures now says "See the details and how it's worked out"; other screens not yet).
- [ ] No complicated charts, formulas or jargon needed to benefit from the product.
- [ ] The explanation, recommendation and follow-up happen naturally in the product, not only in the AI chat
  or after searching several screens.

## 2. Explain the figures in everyday language (§2)

- [x] **Every key figure has a plain sentence** (all 28, website and phone app, Today and Key figures; `backend/app/plain/figures.py`): what changed, whether it is good or bad, why it matters,
  with the number shown underneath. Her example: "Your products are selling faster than last month. That is
  usually positive because less money is sitting in unsold stock. Check that your popular products do not run out."
- [x] Good or bad is worked out from each figure's good direction, with "watch" for figures that are neither (items in stock, stock bought), and a loss is called a loss.
- [~] **Customer churn example:** the sentence is done ("More customers stopped coming back than last month. This could reduce next month's sales."). Still to do: the customer groups most affected and a suggested way to reconnect (comes with the alert card, item 2).
- [x] **Only name a specific cause when the data supports it:** the figure sentences never state a cause (a test checks every sentence); causes stay on the What changed page, with evidence.
- [x] Rename the dashboard category **"Money" to "Financial Health"** (costs, profit, margins and cash flow where
  data exists). Keep **"Sales"** separate. (Website, phone app, alerts, notifications, settings.)
- [~] All other labels: simple words first, technical terms explained when needed. Done for the figures; the alert and What changed wording is still technical (for example "248.9 points below your usual"), to be rewritten with the alert card (item 2).

## 3. The recommendation and follow-up loop (§3: her most important feedback)

She wants this to be the central experience, not separate pages to remember.

- [ ] **1. Detect:** a problem, risk or opportunity is spotted from the business data.
  - [~] Detection exists (changes, alerts, drivers). It is not yet the start of one connected flow.
- [ ] **2. Explain:** what changed, why it matters and what evidence supports it.
  - [~] What changed and evidence exist on the What changed page; not shown with the alert.
- [ ] **3. Recommend:** one or more realistic next steps, each with **High, Medium or Low priority and a brief reason**.
  - [~] Options, ranking and "why this one" exist; the High/Medium/Low label with a reason is not shown.
- [ ] **4. Decide and assign:** the owner can **accept, change, assign, postpone or dismiss**. Accepted actions become tasks immediately.
  - [x] Accept, change before accepting, assign, dates, steps.
  - [~] Dismiss exists for a recommendation.
  - [ ] **Postpone** ("remind me later" with a date).
  - [ ] Dismiss with a reason, remembered for the future.
- [ ] **5. Follow up:** remind the responsible person, check progress, ask for an outcome when needed.
  - [~] Overdue marking and the timed follow-up round exist.
  - [ ] Reminders to the assignee before and on the due date.
  - [ ] On completion, ask "what was done?" (and structured questions such as "how many customers were contacted?").
- [ ] **6. Measure and learn:** compare results before and after, say whether the action appears to have helped, and use the history to improve future suggestions.
  - [x] Expected against actual with the season taken out; four outcomes; track record feeds later suggestions.
  - [ ] Say plainly when the outcome **cannot yet be measured**, never claim success. (The "cannot tell" outcome exists; check every screen and email uses her wording.)
  - [ ] Judge by profit as well as sales where a discount was involved (see example B).
- [ ] **Five stages in the product:** Understand, Explain, Predict, Recommend, Follow Up. Learning happens continuously after follow-up, not as a sixth numbered stage. (Menu and page grouping.)
- [ ] **Her worked flow works end to end:** an alert "Customer return rate fell" opens a recommendation on the same
  screen, accepting it creates a task with a due date and assignee, a few days later Vyterlix reminds them, on
  completion it asks what was done, and later it compares the new return rate where possible.

## 4. Detailed examples (§4): each must work end to end

For each: the insight, why it matters, a recommendation, a priority, the action and assignee, and the follow-up.
Her figures are illustrations, not real results.

- [~] **A. Customers not returning** (26 of 100 came back, against 38). Priority High if the fall is unusually large.
  Recommend reviewing complaints or missed appointments and contacting customers who have not returned in 6 to 8 weeks.
  Follow-up: 14 days ask how many were contacted, 30 days check how many returned, record whether it helped.
  - Exists: "Win back the customers who stopped coming". Missing: the 14 and 30 day questions and the linked card.
- [ ] **B. Best-selling product.** Opportunity, but it could run out. Recommend: check stock, a targeted offer to previous buyers, **avoid unnecessary discounting** if demand is strong. Assign stock review to the owner and the campaign to sales.
  Follow-up after 2 weeks: stock, offer uptake and profit; **do not judge by sales alone** if discounts cut profit.
- [ ] **C. Slow-moving stock.** Selling slowly for three months with high stock. Recommend: reduce or pause the next reorder, review the price, or a limited promotion, **showing which option suits the margins and demand**. Action: stock-review task with a deadline before the next supplier order.
  Follow-up: remaining stock, sales and margin; **if a promotion did not help, do not repeat it automatically**.
- [~] **D. Quiet appointment times** (Tuesday afternoons). Recommend a small Tuesday-only offer or booking reminders; a two-week trial. Follow-up: compare Tuesday bookings **and earnings** before and after; if bookings rose but profit fell, suggest a different approach.
  - Exists: "Run a special on the day that has gone quiet" (from sales). Needs booking data for service businesses.
- [ ] **E. Popular service, weak rebooking.** Recommend offering the next appointment at checkout or a reminder at the normal repeat interval. Follow-up after one month; record whether reminders or staff prompts worked better. (Needs booking data.)
- [ ] **F. Rising cancellations.** Recommend reminders 24 to 48 hours before, or a clear cancellation policy. Follow-up: compare late cancellations over four weeks **and check customers are still satisfied**. (Needs booking data.)
- [~] **G. Costs rising faster than sales.** Recommend reviewing the largest cost increases first; **do not recommend cutting costs blindly** without checking the effect on service quality. Create a review task for the owner or finance lead. Follow-up: did costs fall, and were sales or customer experience affected.
  - Exists: "Go through the cost that grew" and "Get quotes and renegotiate with the supplier". Missing: the quality-of-service check.
- [ ] **H. Valuable customer at risk** (orders less often, lower spend). Recommend the account manager contacts them. High-priority contact task due within three days. Follow-up: record whether contact happened, the feedback and later orders; **avoid claiming the customer was saved** until the outcome is visible.
- [ ] Add the missing recommendations to the library: protect-and-restock the best seller, slow-stock review, valuable customer at risk, rebooking prompt, cancellation reminder.

## 5. Important alerts lead straight to useful actions (§5)

- [ ] A High priority alert shows, immediately and on the same screen: **the reason, the suggested action, the responsible person and the deadline**.
- [ ] Her example alert card works: "High priority: Fewer customers returned this month. Why: return visits fell from 38% to 26%. Suggested next step: contact customers who have not booked in 60 days. Assign to: customer service. Review in: 14 days."
- [ ] The owner can choose **Accept, Edit, Assign or Dismiss** on the card.
- [ ] **Accepting creates a linked action and follow-up.** (Today an alert only links to a screen that explains it.)
- [ ] Not every good change needs a task: good news is explained simply.
- [ ] When there is a meaningful opportunity, recommend how to build on it.
- [ ] **Priorities reflect likely business impact, urgency and strength of evidence, not just the size of a percentage change.**
- [x] Alerts are graded by seriousness and cover sales, customers and stock changes.

## 6. Adapt to the kind of business (§6)

- [ ] **Onboarding asks whether the business sells products, provides services, or both.**
- [ ] Wording, key figures and recommendations adapt to that choice **and to the data actually available**.
- [ ] Retail and wholesale: best sellers, slow stock, reorders, margins, repeat buyers, suppliers. (Largely exists.)
- [ ] Salon, barber and beauty: popular services, repeat bookings, no-shows, cancellations, staff availability, quiet periods.
- [ ] Consultancy and agency: leads, proposals, client renewals, project profitability, unpaid invoices, staff capacity.
- [ ] Cleaning, repair and home services: bookings, repeat contracts, travel or service costs, cancellations, customer satisfaction.
- [ ] Mixed businesses: show product and service insights where relevant, without forcing one set of measures on everything.
- [ ] **No empty "Stock to watch" section** for a business without stock. Offer instead "Bookings to watch", "Customers to follow up" or "Services needing attention".
- [ ] When there is not enough data for a recommendation, **say what is missing**. (Data quality exists; not tied to each recommendation.)
- [ ] New data types needed for service businesses: bookings or appointments, services, staff, cancellations, no-shows, and their import and typing-in. (Very large; see section 0.)

## 7. StableScore AI as a reference, and going further (§7)

- [ ] Presentation: each card clearly shows a **recommended action, the reasons supporting it, and a next step** (as in their example cards: score out of 100, "Why this recommendation?" with three signals, "Next action" with an owner). Do not copy their design.
- [ ] Go further: connect the recommendation to an **action the business can accept and assign**.
- [ ] Track whether it was done.
- [ ] Measure the result when possible.
- [ ] Remember the outcome for future decisions. (Exists in the engine; make it visible: "last time this worked for you".)

## 8. Her six questions: answers owed to her (§8)

- [ ] Is an important alert's recommendation linked directly to an action? **Not yet.** Being built (section 5).
- [ ] When the owner accepts, is the task created and tracked with owner, deadline and reminders? **Created and tracked with owner, dates and overdue marking now; reminders being added.**
- [ ] After completion, how is the outcome checked and used? **Compared with what was expected, with the season removed; feeds later suggestions. Profit-aware checks being added.**
- [ ] Does the business type chosen at onboarding change wording, figures and recommendations? **Not yet.** Planned (section 6).
- [ ] Can the main dashboard explain findings in plain language without the AI assistant? **Partly.** The plain-language layer is planned (section 2).
- [ ] Which parts are working now and which are planned before final handover? Send the working and planned list.
- [ ] Reply to the client in plain English covering the six answers and the plan (a Word file).

## 9. Her final thought (§9)

- [ ] The most important part is easy to see and use: explain the business, recommend practical actions, follow up, measure the outcome, learn from it.
- [ ] She asked for our thoughts on how the current build supports this and what is still being worked on (the reply above).
- [ ] Her stated non-priority: colours and the number of charts. (But see the charts point in section 0.)

## 10. Presentation items from the covering message

- [ ] More **personalised, practical and easy to understand** than other platforms offering similar services.
- [ ] Beyond dashboards, charts and figures: explain, recommend, follow up.
- [ ] The recommendation and follow-up process is the core feature that should make Vyterlix different.

## 11. Charts with the words (from the verbal point, to confirm in section 0)

- [ ] Each alert and recommendation card has a **small chart** (trend with the change marked).
- [ ] Action results show a **before and after chart**.
- [ ] Forecasts show the likely range as a band on a line graph (website; the phone already has a line chart).
- [ ] Today shows a simple picture of the trend and of how actions are doing.
- [ ] One reusable "chart plus one plain sentence" component, used everywhere.

---

## Build order proposed

1. Plain-language layer and the **rename to Financial Health** (sections 2, labels in 5 and 10).
2. **Alert becomes a recommendation card** with Accept / Edit / Assign / Postpone / Dismiss and a linked action (sections 3 and 5), with priority labels and the chart plus sentence component.
3. Reminders, completion questions and profit-aware outcomes (section 3, examples B and D).
4. Today rebuilt around the five questions (section 1).
5. Business type at onboarding and a "no empty stock section" rule (section 6, first part).
6. The missing recommendations (section 4).
7. Service-business data and figures (section 6, last item): its own phase, once the client agrees scope.
