---
title: LLMs Are Ivory-Tower Analysts: Four-Week Lookback at Season 0
nav: Four-Week Lookback at Season 0
eyebrow: Blog · Season 0 report · Oct 25, 2026
author: Chenghao Yang (SSA open-source research contributor)
description: Season 0 report: how SSA scores forecasts, which models and agents lead, and which questions beat everyone.
---
<!--
EDITING NOTES (this comment does not appear on the page)

Edit any text below, then rebuild the page:
    python analysis/season0/build_blog.py
Preview: python -m http.server 8766 --bind 127.0.0.1 (from site/), then open
http://127.0.0.1:8766/blog.html

Plain Markdown:
  ## Heading {#id}     section heading; the id feeds the side navigation
  ### Heading          sub-heading
  **bold**  *italic*  [link text](url)  `code`
  - item / 1. item     bullet or numbered list
  A blank line starts a new paragraph.

Page blocks (open with ::: name, close with a line holding only :::):
  ::: lede             large intro paragraph
  ::: callout Title    boxed summary with a bold title
  ::: cheer Title      green congratulations box
  ::: note             small grey note
  ::: steps            numbered steps; each item is "1. **Bold lead.** Rest."
  ::: cards [five]     cards; each item is "- Tag | Title | Text"
  ::: panel NAME       a chart; edit its title:, sub: and note: lines only.
                       NAME is one of toy, model-board, scatter, full-board,
                       trajectory, weekly, h2h, strip. Panels that follow the
                       bottom bar fill their subtitle automatically.
  [[table: NAME]]      a data table: harness, group or early.
  [[Term]]             dotted term. Hover shows the definition from ::: glossary.
  [^id]  and  [^id]:   footnote marker, and the note itself.
  ::: flow             two-path diagram. Sections are question:, models:,
                       agents:, then one join: line.

`title` is the page headline. `nav` is the short label in the left-hand post list.

Numbers in the text are typed by hand. They come from
site/blog/season-0.json, which analysis/season0/report.py writes.
-->

::: lede
Since Season 0 opened on Sep 30, 43 questions have locked and resolved. This report covers how we score them, who leads, and which questions beat everyone.

The headline: the strongest general-purpose models forecast public opinion like analysts far from the street. They expect change and call it with confidence, while most of these numbers barely move.
:::

::: callout In short
- **H2O.ai SuperAgent** leads Season 0 (S0) with a 67.3 pairwise win rate. **Apodex-FutureFlow** trails by 0.7, a statistical tie. **Vac** leads on [[Original skill]] (+39.3) and on the [[Common view]].[^vac]
- Four of seven agents outperform every single model. **Claude Sonnet 5** is the strongest model in S0. The larger Claude Opus 5 scored 11.8 points lower in the pre-season and does not run in S0.
- **More intelligence ≠ better social simulation.** Across 12 models, a higher Artificial Analysis Intelligence Index went with a lower SSA rank (Spearman −0.37).
- Agents lead the season, and over the season the [[Crowd]] beats [[persistence]] and [[climatology]]. On three weekly approval polls, however, one of those two baselines beat every model and agent. Those polls moved by 0, 2, and 3 points, which calls for more careful temporal modeling.
:::

::: note
Numbers freeze the board at Oct 25, 2026, 20:52 UTC. Season 0 continues, so standings can change. The [live leaderboard](index.html#leaderboard) updates daily. Use the bar at the bottom of the page to switch every chart between the two metrics and three question sets.
:::

## How we score {#scoring}

### Lock first, so the answer cannot leak

Every question follows the same five steps, so nobody can see the answer before forecasting it.

::: steps
1. **Freeze the question.** Before any model or agent submits a prediction, we publish the question text, the population it measures, which future release counts as the answer, and the past values of that series. The flowchart below follows one question down both paths.
2. **One cutoff for everyone.** For most questions the lock is 48 hours before the result is scheduled to publish. Everyone sees the same information.
3. **Seal each forecast.** Entrants sign every forecast. The platform encrypts it, and nobody, including us, reads it before the lock.
4. **Timestamp at the lock.** A manifest of every forecast's SHA-256 hash goes to [OpenTimestamps](https://opentimestamps.org) and lands in a Bitcoin block. Anyone can prove a forecast existed before the answer is revealed.
5. **Take the first release.** When the source first publishes the number, that number is the answer, and we save the file we read. If the source later edits the number, the score stays on the first release and the edit stays visible.
:::

::: flow
question:
- **[[Source]].** Economist/YouGov, the weekly poll that publishes the number.
- **Question.** What percent of US registered voters approve of Donald Trump in the next wave?
- **[[Population]].** US registered voters, the people that percent describes.
- **History.** The past readings come along: 39, 40, 38.
models:
- We attach the last ten readings.
- Web-search setups may look things up.
- Return one distribution, such as 38.5 ± 1.5. A [[profile question]] returns every subgroup. A [[ranking question]] returns an ordered list.
agents:
- The same question arrives as JSON.
- The agent may search and call tools until the lock.
- The answer has the same shape as a model's.
join: Both paths stop 48 hours before the result is published. Then we seal the forecasts, timestamp them, and score the first release.
:::

### How we compute the scores

::: cards
- One number | CRPS | Scores the whole forecast distribution against the true value. Sharp and right wins. Sharp and wrong loses hard.
- Subgroup profile | Energy score | Scores all 16 cells of a demographic breakdown at once, so the pattern across groups counts.
- Ranked list | RBO | Compares top-ten lists and weights the top ranks most. A right #1 counts more than a right #9.
:::

Lower loss is better for all three. Losses from different answer types never mix directly. Two leaderboards turn them into comparable scores:

- **Original skill** = 1 − your loss ÷ persistence loss, shown ×100. Copying the last value scores 0. A perfect forecast scores 100. Below 0 means copying would have done better.
- **Pairwise win rate**: on each question, the share of other methods you beat, with ties worth half. We average this over questions. A missing answer scores 0.

::: panel toy
title: Try it: one question, four forecasts
sub: The last poll read 42.0
note: Forecasts are normal distributions written as mean ± spread. Drag the slider and watch Model B: one confident miss sinks its skill far below zero, while its pairwise score stops at 0.
:::

That gap between the two metrics matters in practice. Claude Sonnet 5 with web search scored −2,880 on [[Original skill]] on one [[16-cell]] profile question. That single answer drags its Season 0 mean skill to −69; without it, the mean would be −2. The same answer cost it at most 2.3 points of pairwise win rate. We lead with pairwise win rate because one blowup cannot decide a season. We keep skill because it measures distance from the truth.

Ranked lists work the same way. Suppose the true top three is A, B, C and you forecast A, C, B. RBO agrees fully at depth 1, half at depth 2, and fully at depth 3. With weights 0.1, 0.09 and 0.081 that gives a similarity of 0.834 and a loss of 0.166.

### How the baselines work

::: cards five
- Anchor | Persistence | Copy the last published value. Skill 0 by definition.
- Line | Trend | Fit a straight line through the last 8 points and extend it.
- Smooth | EWMA | A weighted average that favors recent values.
- Long run | Climatology | The average of the last 24 values.
- Pool | Crowd | An equal-weight pool of every submitted forecast.
:::

### Fair play

- Self-submitted forecasts carry an Ed25519 signature that we verify. Forecasts after the lock never count.
- Test accounts stay off the board.
- Accounts from one team count as one entrant. Vac's four specialist accounts form one team. Anonymous A1–A6 form one team with a fixed priority order, so extra accounts buy no extra chances.
- Season 0 fixes its roster at 24 methods: 7 agents, 12 model setups, 5 baselines. Later entrants appear under All history only.
- An entrant silent for 14 days retires from the board.

## Models {#models}

Six model families run in Season 0. Each runs twice with web search: once with a plain prompt and once with a superforecaster prompt (marked SF).

::: panel model-board
title: Model standings
:::

::: cheer Congratulations
**Claude Sonnet 5** is the strongest single model of Season 0. With the SF prompt, it posts a 56.9 pairwise win rate and +4.6 skill. It is the only model to outrank the Crowd baseline on pairwise win rate. **Gemini 3.1 Pro** leads the models across all history.
:::

### General benchmarks miss social simulation

Do models that ace general benchmarks, or that developers use most, forecast people better? We compared SSA scores with two public signals: the [Artificial Analysis](https://artificialanalysis.ai) Intelligence Index, as a measure of general intelligence, and 7-day token volume on [OpenRouter](https://openrouter.ai), as a measure of popularity. The pre-season window (Aug 28 – Sep 29) puts 12 models on the same 59 questions.

::: panel scatter
title: SSA pairwise win rate against public signals
note: Each dot is one model family; its score averages the plain and SF setups. ρ is Spearman rank correlation. Six or twelve points make every ρ noisy.
:::

The four models with the highest Artificial Analysis scores here, Claude Opus 5, Qwen3.8 Max, Kimi K3 and GPT-5.6 Terra, all finished in the bottom half on SSA. Across all 12, Spearman ρ = −0.37. OpenRouter popularity also leans negative (ρ = −0.17). Neither signal picks the SSA winner.

Apodex-FutureFlow is an agent. We rank it with the agents and leave it out of the model comparisons.

### Does fresher knowledge help?

We took each model's training cutoff from its SSA method card, which records whether the developer declared the date or a third party reported it. Later cutoffs went with *lower* scores (ρ = −0.69 across 10 models). Claude Opus 5's cutoff is four months later than Claude Sonnet 5's, yet Opus scored 11.8 points lower.

**Confounder caution.** Fresher models also differ in size and tuning, and web search hands every model the latest news anyway. We dropped two cutoffs that fall on or after the model's release date. The clean test needs one model in two versions with different cutoffs. Season 0 has none yet, so our analysis code pairs same-vendor models automatically and will rerun as new versions arrive.

### The prompt matters as much as the model

[[table: harness]]

The SF prompt lifted Claude Sonnet 5 and GPT-5.6 Luna by about 6 points and rescued Sonnet's skill. It cost Grok 4.5 nearly 12 points. Test the prompt on SSA before you trust it.

## Agents {#agents}

Seven agent teams compete in Season 0. The chart shows every method on one board.

::: panel full-board
title: Season 0 standings
:::

::: cheer Congratulations
**H2O.ai SuperAgent** leads Season 0 at four weeks: a 67.3 pairwise win rate and +20.4 skill across all 43 questions. **Apodex-FutureFlow** sits 0.7 behind; the 95% interval on that gap runs from −8 to +9, so the race is open. **H2O.ai Marina** holds third. **Vac** posts the best skill (+39.3) and tops the Common view, where every method answered the same 18 questions.
:::

### Who stayed on top

::: panel trajectory
title: Standing over time
note: Each point is the cumulative score through that day.
:::

Apodex-FutureFlow and H2O.ai Marina sat in the top three on all 12 scoring days. Vac led the first week, then skipped questions and slipped. SuperAgent started slowly, won the next two weeks, and has led since Oct 16.

::: panel weekly
title: Week by week
sub: S0 · pairwise win rate on that week's questions
note: Weeks start on Monday and group questions by resolution date. A skipped question scores 0, which explains most of the dips.
:::

### Do agents beat models?

Mostly. Four of seven agents outperform the best model. Below, each agent faces every model on every question both answered.

::: panel h2h
title: Agent against model, head to head
sub: S0 · share of matchups won
:::

Vac wins 78% of its matchups with models; ZhengZheng Forecaster wins 39%. An agent wrapper alone guarantees nothing. The method inside decides.

## Questions {#questions}

Season 0's 43 questions come from six sources. Civiqs daily trackers supply 28 of them.

[[table: group]]

::: panel strip
title: Where models led and where agents led
sub: S0 · one dot per question · hover for the question
note: Height = models' mean pairwise points − agents' mean pairwise points on that question.
:::

### Models led on national numbers and subgroup grids

Models beat agents on NY Fed inflation expectations (DeepSeek V4 Pro won both), the YouGov 16-cell approval crosstabs, the Google Trends search-share basket, and Civiqs Trump approval in mid-October. GLM-5.2 and Grok 4.5 won three of these outright.

### Agents led on the economy

Agents dominated Civiqs economy questions: economic direction, family finances and inflation concern. On the Oct 16 family-finances question, agents beat models by 50 points.

### The week nothing moved

Three Civiqs economy questions resolving Oct 9 ended within 0.5 points of their last reading. Most methods predicted a change and lost to persistence; the median skill fell to −58, −60 and −84. On the Oct 13 Economist/YouGov registered-voter approval question, the value held at exactly 38.0, and no model or agent beat persistence. On two more YouGov approval questions, climatology won. Weekly approval polls wobble by a point or two, and a confident call for change costs more than it earns.

### Can the first days pick the winner?

[[table: early]]

The first week found the contenders: two of the final top three, and a rank correlation of 0.78. Two weeks fixed the whole top three. The lead itself changed hands four times and is still a tie. Early standings forecast the field well and the champion poorly. **The board has to stay live.** A static benchmark would have frozen an early ranking and missed those lead changes.

## Lessons {#lessons}

### For builders

1. **Answer every question.** A skipped question scores 0 in pairwise. Vac posts the best record on the questions it answers, yet skipped 12 and ranks fourth.
2. **Avoid confident misses.** Match your spread to the series' real weekly movement. When a poll barely moves, stay close to its last value.
3. **Model subgroups together.** Profiles and crosstabs are where most agents trail plain models.
4. **Choose models by SSA results.** Benchmark rank and popularity pointed the wrong way here. Test your prompt as well: one prompt change moved Grok 4.5 by 12 points.

### For the platform

1. **Widen the question mix.** One source supplies 28 of 43 questions. We are adding sources and our own measurements so no single tracker decides a season.
2. **Publish uncertainty.** We will add intervals to win rates so near-ties read as ties.
3. **Keep both metrics and the Common view.** Pairwise resists blowups, skill measures accuracy, and Common compares everyone on identical questions.
4. **Raise the bar.** Next come questions with no history, policy what-ifs, and finer subgroups. These test whether a simulator understands a population.
5. **Keep the rules fixed.** A frozen roster, sealed forecasts and public timestamps keep a harder arena fair.

Want your method on the board? [Start here](docs.html#quickstart). Questions or corrections: [open an issue](https://github.com/Social-Atoms/social-sim-arena/issues) or write to [hi@social-atoms.org](mailto:hi@social-atoms.org).

[^vac]: Vac skipped 12 questions. Pairwise counts a skip as zero, so on that board Vac ranks fourth of the full field.

::: glossary
- Original skill | 1 − your loss ÷ the persistence loss, shown ×100. Copying the last value scores 0. A perfect forecast scores 100. Below 0 means copying the last value would have done better.
- Common view | The board restricted to questions every Season 0 method answered. Here that is 18 questions, so a skipped question cannot change the order.
- 16-cell | One approval question broken into sixteen demographic subgroups and scored together. Civiqs uses party, age, race, education and gender. YouGov uses the survey's own sixteen crosstabs.
- profile question | Forecast every subgroup at once, not only the national number. Season 0 scores these with the energy score.
- ranking question | Put items in order, such as the week's most-read Wikipedia pages. Rank-biased overlap compares the lists and weighs the top ranks more.
- Source | The outside publisher that releases the measured number, such as Economist/YouGov, Civiqs, or the NY Fed.
- Population | The people the question measures. In the sample, US registered voters.
- persistence | The baseline that copies the last published value of the series.
- climatology | The baseline that predicts the average of the last 24 published values.
- Crowd | Every submitted forecast pooled with equal weight, then scored like any other method.
:::
