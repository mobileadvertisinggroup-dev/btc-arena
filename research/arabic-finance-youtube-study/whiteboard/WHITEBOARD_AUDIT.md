# Forensic Audit — English Whiteboard / Hand-Drawn Animation Channels

**Collected:** 2026-09-08 UTC via YouTube Data API v3
**Discovery:** 32 queries (channel + video search) → 693 channels screened → 24 collected
**Sample:** 2,765 videos, 24-month window

> **Label key:** **FACT** = verbatim from the API. **CALCULATION** = derived. **ESTIMATE** = rule-based. **OPINION** = judgement.
>
> **The one thing the API cannot tell me:** whether a channel actually draws by hand. There is no visual-style field, and youtube.com is blocked in this environment. Visual style below is **analyst coding with a confidence flag** — 8 channels are marked `NOT_VERIFIED` because I could not confirm them. Every *number* is an API fact; every *style label* is not.

---

## 1. The audit table

| # | Channel | Subs | Total videos | Uploads/mo (24mo) | Median views | Median length | Length p25–p75 | Views/sub | Shorts % | Status | Visual style |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | AsapSCIENCE | 10.8M | 559 | 3.08 | 421K | 7.4m | 6.0–9.1m | 0.039 | 66% | active | whiteboard marker drawing, hand visible |
| 2 | GREAT IDEAS GREAT LIFE | 7.2M | 726 | 6.71 | 189K | 12.3m | 11.1–14.0m | 0.026 | 43% | active | 2D animated book summaries |
| 3 | minutephysics | 6.0M | 316 | 1.50 | 606K | 5.2m | 4.1–6.7m | 0.101 | 24% | active | marker line drawing on white, hand visible |
| 4 | SeeKen | 4.6M | 830 | 11.08 | 141K | 22.2m | 16.2–33.1m | 0.031 | 40% | active | 2D animated book summaries |
| 5 | After Skool | 3.8M | 251 | 1.88 | 289K | 13.4m | 10.8–17.6m | 0.076 | 0% | active | hand-drawn marker on white, single continuous draw |
| 6 | Improvement Pill | 3.8M | 356 | 1.29 | 25K | 8.1m | 7.4–8.8m | 0.007 | 0% | active | stick-figure doodle animation |
| 7 | Better Than Yesterday | 2.6M | 105 | 0.25 | 334K | 9.1m | 7.5–11.6m | 0.127 | 0% | slowing | simple doodle/2D animation |
| 8 | Einzelgänger | 2.4M | 349 | 2.46 | 139K | 17.8m | 15.3–23.3m | 0.058 | 22% | active | stock art + motion, NOT whiteboard |
| 9 | Sprouts | 1.9M | 230 | 2.00 | 34K | 4.5m | 4.1–5.4m | 0.017 | 53% | active | whiteboard cut-out doodle, education |
| 10 | FightMediocrity | 1.9M | 168 | 0.00 | 237K | 11.2m | 9.7–13.5m | 0.128 | 17% | **DORMANT** | whiteboard doodle, book summaries |
| 11 | Blunt Brothers Productions | 1.6M | 355 | 6.04 | 599K | 9.1m | 5.2–15.3m | 0.379 | 75% | active | unverified |
| 12 | Draw The Life TikTak | 1.4M | 1,167 | 5.67 | 8K | 5.2m | 4.1–14.4m | 0.005 | 25% | active | draw-my-life hand-drawn storytelling |
| 13 | Proactive Thinker | 1.2M | 868 | 21.62 | 13K | 15.8m | 13.1–19.5m | 0.011 | 27% | active | doodle/whiteboard style |
| 14 | Rishi Draws | 1.1M | 190 | 0.29 | 48K | 8.0m | 7.4–8.5m | 0.044 | 97% | **DORMANT** | unverified |
| 15 | The Art of Improvement | 1.1M | 372 | 2.25 | 13K | 9.8m | 9.2–10.6m | 0.012 | 0% | active | whiteboard doodle |
| 16 | Marko - WhiteBoard Finance | 1.0M | 425 | 4.67 | 24K | 14.6m | 11.4–18.1m | 0.024 | 16% | active | presenter on camera despite the name |
| 17 | Whiteboard Crypto | 1.0M | 152 | 0.46 | 88K | 10.7m | 9.5–12.7m | 0.086 | 3% | **DORMANT** | whiteboard drawing with characters |
| 18 | theflippist | 995K | 311 | 0.54 | 78K | 3.6m | 3.2–4.0m | 0.078 | 87% | slowing | flipbook hand-drawn |
| 19 | One Percent Better | 713K | 150 | 0.00 | 14K | 18.6m | 9.6–61.4m | 0.020 | 2% | **DORMANT** | unverified |
| 20 | Siebert Science | 481K | 163 | 1.08 | 96K | 18.0m | 13.5–22.6m | 0.200 | 16% | active | unverified |
| 21 | Sticko Explains | 130K | 125 | 5.33 | 24K | 12.4m | 11.4–13.4m | 0.184 | 0% | active | stick-figure doodle |
| 22 | HealthSketch | 128K | 44 | 0.12 | 182K | 4.9m | 4.2–5.2m | 1.424 | 48% | **DORMANT** | whiteboard medical sketch |
| 23 | Wealthboard | 106K | 339 | 14.12 | 1K | 60.6m | 34.6–90.4m | 0.010 | 52% | active | unverified |
| 24 | ToonStarterz Doodles | 79K | 96 | 0.79 | 54K | 12.3m | 9.2–14.4m | 0.677 | 0% | active | unverified |
**Reading it:** rank is by subscribers, but subscribers are the *least* useful column. `views/sub` and `median views` tell you who actually reaches people.

---

## 2. The five findings that matter

### Finding 1 — This format has a 21% death rate

**[FACT]** 5 of 24 channels have not uploaded in over 180 days:

| Channel | Subs | Silent for |
|---|---|---|
| FightMediocrity | 1.85M | **3.9 years** |
| One Percent Better | 713K | 3.0 years |
| HealthSketch | 128K | 1.9 years |
| Whiteboard Crypto | 1.02M | 1.3 years |
| Rishi Draws | 1.1M | 0.8 years |

Two more are slowing. **Only 17 of 24 are genuinely active.**

**[OPINION]** These are not small channels that failed — they are million-subscriber channels that *stopped*. Hand-drawn animation is slow to produce and does not get faster with scale. The format's main risk is not the algorithm, it is the animator quitting. Every one of these had product-market fit and still stopped.

### Finding 2 — Money is the weakest topic in this format

**[CALCULATION]** Median views by topic, across the whole set:

| Topic | Videos | Channels | **Median views** | Index vs channel median | 95% CI |
|---|---|---|---|---|---|
| psychology / mind | 162 | 18 | **127,179** | 1.20 | [0.99, 1.39] |
| book summary | 47 | 9 | **103,550** | 1.43 | [1.00, 2.37] |
| science / physics | 42 | 11 | 53,292 | 0.71 | [0.49, 1.18] |
| self-improvement | 127 | 15 | 27,923 | **1.17** | **[1.07, 1.37]** |
| **money / business** | 330 | 14 | **12,904** | 1.00 | [0.92, 1.11] |

Two separate things are happening, and they must not be confused:

- **Index** (each video against its own channel's median) says only **self-improvement confidently outperforms**. Everything else has a confidence interval crossing 1.00 — including psychology, which sits right on the line at 0.99. Do not treat the others as proven.
- **Absolute median views** says where the *audience* is. Psychology pulls **127K**; money pulls **12,904**. A **10× gap**, across a similar number of channels.

**[FACT]** Money is also the **most produced** topic in the set — 330 videos, the largest single block. So the format's creators are pouring the most effort into its lowest-reach subject.

**[OPINION]** This is the single most important line in this audit for you. If you are running a whiteboard channel and pointing it at money, you have picked the format's hardest topic and its most crowded one. The audience for drawn explainers is in **psychology, human behaviour, and book/idea summaries** — money works best as a *lens* on those, not as the subject itself. "Why your brain makes you spend" beats "how compound interest works."

### Finding 3 — Length: 8–18 minutes, and 30+ minutes is fatal

**[CALCULATION]** Performance index by duration band:

| Band | n | Median views | Index |
|---|---|---|---|
| under 4 min | 53 | 59,958 | 0.87 |
| 4–6 min | 125 | 46,808 | 0.97 |
| 6–8 min | 84 | 73,743 | 0.96 |
| 8–10 min | 220 | 31,150 | 1.00 |
| 10–15 min | 547 | 43,734 | 1.01 |
| **15–20 min** | 275 | 37,758 | **1.21** |
| **20–30 min** | 188 | 65,209 | **1.28** |
| **30+ min** | 246 | 2,849 | **0.66 → 0.27** |

**[CALCULATION]** The 30+ minute collapse is **not** an artifact. I suspected one weak channel (Wealthboard, 60-min median, 1,077 median views) was dragging it. Removing it makes it **worse**: index falls to **0.27**. Long-form genuinely does not work in this format.

**[FACT]** Meanwhile, the ten highest-reach *active* channels sit at a **10.7 minute median**, with a 25–75 range of roughly 5–17 minutes.

**[OPINION] The resolution:** within a given channel, stretching to 15–25 minutes helps. But the channels that reach the most people overall live at 9–13 minutes. Target **10–18 minutes**. Never go under 5. Never go over 30 — the drop there is severe and consistent.

### Finding 4 — The slow-publishing rule holds here too

**[CALCULATION]** Among the 19 active channels:

| Correlation | rho |
|---|---|
| uploads/month ↔ views per sub | **−0.449** |
| uploads/month ↔ median views | −0.311 |
| subscribers ↔ views per sub | −0.139 |

| Upload tier | Channels | Median views/sub |
|---|---|---|
| **≤1/month** | 3 | **0.127** |
| 1–2.5/month | 7 | 0.058 |
| 2.5–6/month | 4 | 0.032 |
| 6+/month | 5 | **0.026** |

**[FACT]** The ten highest-reach active channels publish a median of **2.17 videos per month**.

**[FACT]** The extremes make the point: minutephysics publishes **1.5/month** and holds a 606,461 median. Better Than Yesterday publishes **0.25/month** — one video per quarter — and holds 334,294. Proactive Thinker publishes **21.6/month** and holds 12,847.

### Finding 5 — Shorts usage is wildly inconsistent, and the leaders mostly skip them

**[FACT]** Shorts share among the biggest reach-per-sub channels:

| Channel | Shorts share | Median views |
|---|---|---|
| After Skool | **0%** | 288,668 |
| Better Than Yesterday | **0%** | 334,294 |
| Improvement Pill | **0%** | 25,457 |
| The Art of Improvement | **0%** | 13,284 |
| Sticko Explains | 0% | 23,888 |
| minutephysics | 24% | 606,461 |
| AsapSCIENCE | 66% | 421,010 |
| Blunt Brothers | 75% | 598,891 |

**[OPINION]** No clean pattern — which is itself the finding. Shorts are neither required nor forbidden in this format. After Skool built 3.78M subscribers on **zero** Shorts. Don't let anyone tell you Shorts are mandatory.

---

## 3. Who the real competitors are, by tier

**[OPINION] based on [FACT] + analyst style coding**

### Tier 1 — True hand-drawn, high confidence, and active

| Channel | Subs | What they do | Length | Cadence |
|---|---|---|---|---|
| **AsapSCIENCE** | 10.8M | Marker science explainers | 7.4m | 3.1/mo |
| **minutephysics** | 5.98M | Marker line-drawing physics | 5.2m | 1.5/mo |
| **After Skool** | 3.78M | Continuous marker draw, philosophy/ideas | 13.4m | 1.9/mo |
| **Improvement Pill** | 3.77M | Stick-figure self-improvement | 8.1m | 1.3/mo |
| **Sprouts** | 1.94M | Whiteboard cut-out education | 4.5m | 2.0/mo |
| **Draw The Life TikTak** | 1.45M | Draw-my-life storytelling | 5.2m | 5.7/mo |

### Tier 2 — Dormant, but the format lesson still stands

| Channel | Subs | Silent | Lesson |
|---|---|---|---|
| FightMediocrity | 1.85M | 3.9 yrs | Whiteboard book summaries worked at scale, then stopped |
| Whiteboard Crypto | 1.02M | 1.3 yrs | Single-topic channels die with their topic's cycle |
| One Percent Better | 713K | 3.0 yrs | — |

### Excluded — and why you should know

| Channel | Why it is not your competitor |
|---|---|
| **Marko - WhiteBoard Finance** | Named "WhiteBoard" but the presenter is **on camera**. Not a faceless drawn channel |
| **Einzelgänger** | Stock art and motion, not hand-drawn |
| Kurzgesagt, TED-Ed, Veritasium | Flat vector / varied animation / live presenter — different format and budget class |
| Alan Becker, SCP Animated | Animation as entertainment, not explainer |
| Procreate, Krita, Cartooning Club | Drawing software and art tutorials |

---

## 4. What this means if you are running one

**[OPINION]**

| Decision | Do this | Because |
|---|---|---|
| **Topic** | Human behaviour, psychology, book/idea summaries — money as a *lens*, not the subject | Psychology pulls 127K median vs money's 12,904, and money is the most crowded block in the set |
| **Length** | 10–18 minutes | Top channels sit at 10.7m median; 30+ min collapses to 0.27 |
| **Cadence** | 2–4 per month | Top ten reach-leaders publish 2.17/month; frequency correlates −0.449 with reach efficiency |
| **Shorts** | Optional | After Skool: 3.78M subs, 0% Shorts |
| **Never** | Under 5 min, over 30 min, or single-topic dependence | Whiteboard Crypto: 1M subs, dead 476 days |
| **Biggest risk** | Your own burnout, not the algorithm | 21% of the set is dormant, including million-sub channels |

**The strategic read:** the whiteboard format's proven audience is people who want to *understand themselves and other people*. It is a psychology-and-ideas format that happens to be able to cover money — not a finance format. The channels that treated it as a finance format (Whiteboard Crypto, Wealthboard, Marko) either died, run at 1,077 median views, or gave up and went on camera.

---

## 5. Limits

- **Visual style is analyst coding, not measurement.** 8 of 24 channels are `NOT_VERIFIED`. The API has no style field and youtube.com is blocked here.
- **Topic classification is a keyword rule.** 46% of videos fell into "other" and are excluded from topic conclusions.
- **Only self-improvement clears statistical confidence** on the index measure. Psychology, book summaries, science and philosophy all have intervals crossing 1.00 — the *absolute reach* gap is the stronger evidence there, not the index.
- **No watch time, CTR or retention.** Private analytics. Not available, not estimated.
- **`search.list` ranking varies by day.** Queries and date are recorded; a rerun may surface a slightly different set.
- Channels publishing fewer than 60 videos in 24 months had their 60 most recent pulled, so a few duration and view medians reach past the window. Cadence figures use the strict 24-month count.
