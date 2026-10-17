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

- [x] **Charts and graphs over words.** DECIDED (owner, 17/10/2026): charts alongside the plain words, which is what is built.
  (earlier note:) Raised verbally; the written feedback says words on top of
  charts ("beyond the dashboards, charts and figures…"). Agree the rule: a chart first, one plain
  sentence under it. Ask which screens most need charts (suggested: alerts and Today).
- [x] **Business types to support first.** DECIDED (owner, 17/10/2026): all small and medium businesses, so every type needs measures, not just retail. (earlier note:) (suggest retail plus one service type, such as salon or barber).
- [?] **Is service-business data (bookings, appointments) in the original scope?** It changes the data
  model; check the contract and milestones.
- [?] **Her question list (§8)**: send her honest answers (see section 8 below) and what is planned.

---

## 1. The main purpose (§1)

- [ ] Vyterlix feels like a business analyst and practical adviser, not a set of charts and formulas.
- [x] **The first screen (Today) answers five questions** (website and phone app; `GET /dashboard` now carries `going_well`, `do_first`, `waiting_for` and `results`):
  - [x] What is going well? (health up, figures in your favour, actions that worked in the last 60 days)
  - [x] What needs my attention today?
  - [x] What should I do first? (the most serious thing, with a button to open it)
  - [x] What am I waiting for? (work under way, work a manager put forward, results still to be checked)
  - [x] Did my previous actions help? (one plain line, and the latest results)
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
- [x] All other labels: simple words first, technical terms explained when needed. The What changed page now uses the same plain words as the alert card (headline, what happened, the figures, why it matters), and the technical wording ("324 points below your usual", "75 out of 100") is gone, website and phone. (earlier note:) Done for the figures; the alert and What changed wording is still technical (for example "248.9 points below your usual"), to be rewritten with the alert card (item 2).

## 3. The recommendation and follow-up loop (§3: her most important feedback)

She wants this to be the central experience, not separate pages to remember.

- [x] **1. Detect:** a problem, risk or opportunity is spotted from the business data.
  - [x] Detection (changes, alerts, drivers) now starts one connected flow: the alert opens as a card (website and phone).
- [x] **2. Explain:** what changed, why it matters and what evidence supports it.
  - [x] The alert card says what happened, the figures behind it, why it matters, the season note, a chart with the month marked, and up to three pieces of evidence. The What changed page keeps the full evidence.
- [x] **3. Recommend:** one or more realistic next steps, each with **High, Medium or Low priority and a brief reason** (on the alert card, and now on the What changed page too).
  - [x] Options, ranking and "why this one"; the priority label with its reason on both screens.
  - [x] For figures that cannot be broken down (appointments, customers coming back) the suggestion comes from the figure's own move, and says it does not know why.
- [x] **4. Decide and assign:** the owner can **accept, change, assign, postpone or dismiss**. Accepted actions become tasks immediately.
  - [x] Accept, change before accepting, assign, dates, steps.
  - [x] Dismiss, with the reason kept (website card).
  - [x] **Postpone** ("remind me later": 3 days, 1 week, 2 weeks or a chosen day, up to 90 days; the alert leaves what needs attention and comes back by itself; website and phone app).
  - [x] Dismiss with a reason, **remembered**: what was said no to is not suggested again for 120 days, and the next suggestion says "You said no to ... on dd/mm/yyyy".
- [x] **5. Follow up:** remind the responsible person, check progress, ask for an outcome when needed.
  - [x] Overdue marking and the timed follow-up round.
  - [x] Reminders to the assignee 2 days before and on the due date, once each (`backend/app/services/reminders.py`); late work stays with the overdue alert.
  - [x] On completion (or part completion), ask "what did you do?" and "how many did it reach?" (website and phone app); a check-in two weeks after finishing asks how it is going.
- [x] **6. Measure and learn:** compare results before and after, say whether the action appears to have helped, and use the history to improve future suggestions.
  - [x] Expected against actual with the season taken out; four outcomes; track record feeds later suggestions.
  - [x] Says plainly when the outcome **cannot yet be measured**, never claims success: the label is now "Cannot be measured yet" on every screen, report, answer and the phone.
  - [x] Judge by profit as well as sales where a discount or spend was involved (`profit_check`).
  - [x] Protecting something that is going well (the best seller) is judged by whether the good result held.
- [x] **Five stages in the product:** Understand, Explain, Predict, Recommend, Follow Up. The website menu and the phone's More menu are grouped by them (Today, Key figures, Health and Reports; What changed and Ask Vyterlix; Forecast; Alerts; Actions and What we know). Learning happens continuously after follow-up, not as a sixth stage.
- [x] **Her worked flow works end to end** (`backend/tests/test_worked_flow.py`): the alert "Customers who came back" fell from 38 to 26 and opens a recommendation on the same card ("Win back the customers who stopped coming"); accepting creates a task with an owner and due date; two days before it is due the person is reminded; on completion it asks what was done and how many were reached; later it compares the new figure and records the result in plain words.

## 4. Detailed examples (§4): each must work end to end

For each: the insight, why it matters, a recommendation, a priority, the action and assignee, and the follow-up.
Her figures are illustrations, not real results.

- [~] **A. Customers not returning** (26 of 100 came back, against 38). Priority High if the fall is unusually large.
  Recommend reviewing complaints or missed appointments and contacting customers who have not returned in 6 to 8 weeks.
  Follow-up: 14 days ask how many were contacted, 30 days check how many returned, record whether it helped.
  - Exists: "Win back the customers who stopped coming". Missing: the 14 and 30 day questions and the linked card.
- [x] **B. Best-selling product.** Opportunity, but it could run out. Recommend: check stock, a targeted offer to previous buyers, **avoid unnecessary discounting** if demand is strong. Assign stock review to the owner and the campaign to sales.
  Follow-up after 2 weeks: stock, offer uptake and profit; **do not judge by sales alone** if discounts cut profit.
- [x] **C. Slow-moving stock.** Selling slowly for three months with high stock. Recommend: reduce or pause the next reorder, review the price, or a limited promotion, **showing which option suits the margins and demand**. Action: stock-review task with a deadline before the next supplier order.
  Follow-up: remaining stock, sales and margin; **if a promotion did not help, do not repeat it automatically**.
- [~] **D. Quiet appointment times** (Tuesday afternoons). Recommend a small Tuesday-only offer or booking reminders; a two-week trial. Follow-up: compare Tuesday bookings **and earnings** before and after; if bookings rose but profit fell, suggest a different approach.
  - Exists: "Run a special on the day that has gone quiet" (from sales). Needs booking data for service businesses.
- [x] **E. Popular service, weak rebooking.** (Done: "Your services" lists what people book, most popular first, with cancelled, missed and booked-again, and says so when few people for one service book again; the recommendation "Offer the next appointment before they leave" is there for the overall fall.) Recommend offering the next appointment at checkout or a reminder at the normal repeat interval. Follow-up after one month; record whether reminders or staff prompts worked better. (Needs booking data.)
- [x] **F. Rising cancellations.** Recommend reminders 24 to 48 hours before, or a clear cancellation policy. Follow-up: compare late cancellations over four weeks **and check customers are still satisfied**. (Needs booking data.)
- [~] **G. Costs rising faster than sales.** Recommend reviewing the largest cost increases first; **do not recommend cutting costs blindly** without checking the effect on service quality. Create a review task for the owner or finance lead. Follow-up: did costs fall, and were sales or customer experience affected.
  - Exists: "Go through the cost that grew" and "Get quotes and renegotiate with the supplier". Missing: the quality-of-service check.
- [x] **H. Valuable customer at risk** (orders less often, lower spend). Recommend the account manager contacts them. High-priority contact task due within three days. Follow-up: record whether contact happened, the feedback and later orders; **avoid claiming the customer was saved** until the outcome is visible.
- [~] Missing recommendations added to the library: protect the best seller, slow-stock review, valuable customer at risk (all three done and tested). Rebooking prompt, appointment reminders and a cancellation policy are also done (they act on the appointment figures themselves, since those cannot be split into causes).

## 5. Important alerts lead straight to useful actions (§5)

- [x] A High priority alert shows, immediately and on the same screen: **the reason, the suggested action, the responsible person and the deadline** (the alert card, website: `frontend/js/alert-card.js`, `GET /alerts/{id}/card`). The phone app has the same card.
- [~] Her example alert card works (priority and reason, suggested step, assign to, due by, and "we will check from" are all on the card; the customer-return suggestion needs the missing recommendations): "High priority: Fewer customers returned this month. Why: return visits fell from 38% to 26%. Suggested next step: contact customers who have not booked in 60 days. Assign to: customer service. Review in: 14 days."
- [x] The owner can choose **Accept, Edit, Assign, Postpone or Dismiss** on the card (website and phone app).
- [x] **Accepting creates a linked action and follow-up** (the alert is marked as being dealt with; a manager outside their area can only propose it).
- [x] Not every good change needs a task: good news is explained simply ("nothing needs fixing").
- [x] When there is a meaningful opportunity, recommend how to build on it (a best seller that is driving a rise: keep it in stock, tell past buyers, do not discount).
- [x] **Priorities reflect likely business impact, urgency and strength of evidence, not just the size of a percentage change** (`backend/app/plain/priority.py`: 40% impact, 30% urgency, 30% evidence; thin evidence caps it at Medium).
- [x] Alerts are graded by seriousness and cover sales, customers and stock changes.

## 6. Adapt to the kind of business (§6)

- [x] **Onboarding asks whether the business sells products, provides services, or both.** ("What do you sell?" in business details.)
- [~] Key figures and forecasts adapt to that choice (services: no stock figures). Recommendations adapt in item 6 and 7.
- [ ] Retail and wholesale: best sellers, slow stock, reorders, margins, repeat buyers, suppliers. (Largely exists.)
- [~] Salon, barber and beauty: repeat bookings, no-shows and cancellations now have figures. Popular services are done ("Your services"), and services and staff are offered as you type them in (website and phone). Staff availability and quiet periods still to do.
- [~] Consultancy and agency: **leads, proposals, wins and client renewals** (type in a lead or proposal on the website and the phone; figures: new leads, proposals sent, proposals won, work won, clients who renewed; suggestions: look back at lost proposals, ask for introductions, talk before renewal) and **unpaid invoices** (money owed, overdue, days to get paid; a sale can have a due day and a paid day, typed in or from a file) are done. **Project profitability and staff capacity** need time and project records that do not exist yet: not started.
- [~] Cleaning, repair and home services: jobs are appointments (typed in or imported from a file), with kept, cancelled, missed and booked-again figures, popular services, money owed, and costs. **Customer satisfaction** (ratings) and **repeat contracts** as such are not started.
- [x] Mixed businesses: product and service figures both show; each set only appears when the business has that kind of record (stock, appointments, invoices, leads).
- [x] **No empty "Stock to watch" section** for a business that sells only a service (website and phone). "Customers to follow up" is shown instead. ("Bookings to watch" waits for booking data, item 7.)
- [x] When there is not enough data for a recommendation, **say what is missing**: the alert card lists what would help (add appointments, say which customer bought, add stock records, fill gaps in the data, record the product on each sale), website and phone.
- [~] New data types for service businesses. **Appointments can now be typed in** (website: Type in data, Appointment tab; service, day, what happened, customer, who, day cancelled). Figures from them: appointments kept, cancelled, missed, late cancellations, customers who booked again. Also done: a services and staff pick-list, and typing in an appointment on the phone. Appointments can also be **imported from a file** (CSV or Excel), with the same checks and undo as sales.

## 7. StableScore AI as a reference, and going further (§7)

- [x] Presentation: each card clearly shows a **recommended action, the reasons supporting it, and a next step** (the alert card: priority and reason, "why this one", the evidence, the steps, who and by when, and "we will check from"). Our own design, not a copy.
- [x] Go further: the recommendation becomes an **action the business can accept and assign** (one tap, or with changes).
- [x] Track whether it was done (status, steps, overdue marking, reminders, what was done and how many it reached).
- [x] Measure the result when possible (before and after, with the season taken out; profit-aware; "cannot be measured yet" when it cannot).
- [~] Remember the outcome for future decisions: the engine uses it and the recommendation evidence says "Last time: ..." and what was said no to. The card shows up to three evidence lines, so it can be left off the card when there are others.

## 8. Her six questions: answers owed to her (§8)

- [x] Is an important alert's recommendation linked directly to an action? **Yes.** The alert opens as a card with the suggestion on it, and accepting it makes the action (website and phone).
- [x] When the owner accepts, is the task created and tracked with owner, deadline and reminders? **Yes.** Created with an owner and dates, reminded two days before and on the day, marked overdue, and a check-in two weeks after it is finished.
- [x] After completion, how is the outcome checked and used? **Compared with what was expected, with the season removed and profit checked where it matters; said plainly when it cannot be measured yet; fed back into later suggestions.**
- [x] Does the business type chosen at onboarding change wording, figures and recommendations? **Yes for figures and sections** (no stock for a service; customers to follow up; appointment figures) **and recommendations** (reminders, cancellation policy, rebooking). Wording by business type is still the same plain wording for all.
- [x] Can the main dashboard explain findings in plain language without the AI assistant? **Yes.** Every figure has a plain sentence, and Today answers her five questions.
- [x] Which parts are working now and which are planned before final handover? See the reply (docs/Vyterlix-response-to-feedback.docx, to be brought up to date) and the build list at the end of this file.
- [ ] Reply to the client in plain English covering the six answers and the plan (a Word file). **The earlier version says several things are "being built" that are now done; it needs rewriting before it is sent.**

## 9. Her final thought (§9)

- [~] The most important part is easy to see and use: explain the business, recommend practical actions, follow up, measure the outcome, learn from it. (Built; wants her to try it and say.)
- [ ] She asked for our thoughts on how the current build supports this and what is still being worked on (the reply above).
- [x] Her stated non-priority: colours and the number of charts. (The tea green theme is done; charts are in section 11.)

## 10. Presentation items from the covering message

- [~] More **personalised, practical and easy to understand** than other platforms offering similar services (a matter for her to judge).
- [x] Beyond dashboards, charts and figures: explain, recommend, follow up.
- [x] The recommendation and follow-up process is the core feature: the five stages, the alert card and the worked flow.

## 11. Charts with the words (from the verbal point, to confirm in section 0)

- [x] Each alert and recommendation card has a **small chart** (trend with the change marked; website and phone).
- [x] Action results show a **before and after chart** (website and phone).
- [x] Forecasts show the likely range as a band on a line graph (website and phone).
- [x] Today shows a simple picture of the trend (how the first key figure has moved, with the latest finished month marked).
- [x] Today also shows how actions are doing as a picture: one bar of worked / partly worked / did not work / cannot be measured yet, with the same words under it (website and phone).
- [~] One reusable "chart plus one plain sentence" component, used everywhere (the same bar chart is used on the card, Today and results, each with its own sentence).

---

## Build order proposed

1. Plain-language layer and the **rename to Financial Health** (sections 2, labels in 5 and 10).
2. **Alert becomes a recommendation card** with Accept / Edit / Assign / Postpone / Dismiss and a linked action (sections 3 and 5), with priority labels and the chart plus sentence component.
3. Reminders, completion questions and profit-aware outcomes (section 3, examples B and D).
4. Today rebuilt around the five questions (section 1).
5. Business type at onboarding and a "no empty stock section" rule (section 6, first part).
6. The missing recommendations (section 4).
7. Service-business data and figures (section 6, last item): its own phase, once the client agrees scope.
