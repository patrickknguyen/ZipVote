# ZipVote Design Heuristics

ZipVote's agents write a voter questionnaire from public sources. These are the principles they follow, broken down into heuristics, and how each one shows up in the product.

## When principles conflict

They are ranked, and the higher one wins. **Earn trust** comes first, then **Inform, don't steer**, then **Start simple, go deep on demand**. A results page would look cleaner with the unknowns hidden, but trust wins, so they stay.

## The heuristics

| Principle | How do we do that? | What does that look like? |
|---|---|---|
| **Earn trust.** Every claim can be checked. | **1. Say only what you can point to.** | Every claim about a candidate links to a quote copied word for word from a source. If the quote can't be found, the claim is dropped. |
| | **2. Don't fill the silence.** | A candidate with no stated position shows "Not enough public information," never a neutral middle score, and never counts toward a match. |
| | **3. Don't outrun the evidence.** | A qualified stance is never rounded up to full support. A specific proposal can take a side on a broader debate (capping developer fees is a side on making housing cheaper to build), but a broad goal never takes a side on a specific policy (supporting affordable housing says nothing about rent control). Results show how much evidence each match rests on ("agrees on 2 of 2 stated" vs "7 of 10"), never one score that hides it. |
| | **4. Let people speak for themselves.** | A candidate's own words outrank what others write about them, and no position is ever assumed from party. |
| **Inform, don't steer.** The tool helps you make a choice. It never makes one for you. | **5. Ask without leaning.** | Each question states one policy and no reason for it. "Raise the minimum wage," not "Raise the minimum wage so families can afford rent." |
| | **6. Give everyone equal footing.** | Every candidate's checked positions go into the question pool and onto their results card, however many web pages they have. Questions are spent where they inform about the most candidates, not split into equal counts. |
| **Start simple, go deep on demand.** The surface stays light, and every layer can be opened. | **7. Write for someone new.** | One short sentence per question, no jargon. "Learn more" explains only the words the question uses. |
| | **8. Spend attention where it tells you the most.** | Every voter starts with the same core, up to 15 questions: debates where the most candidates take a side, opposite sides first. A deep dive follows, shaped only by the voter's own answers, never their party or anything else about them. It asks where candidates the evidence can't rule out differ, and where least is known about them. No candidate is ever ruled out, every question says why it was asked, and you can stop whenever you choose. |
| | **9. Start from what the user already knows.** | Before any issue questions, ask which ballot you'll get. "I don't know" is an answer, not a dead end. The deep dive starts from the issues you answered most strongly, and one tap changes them. |
| | **10. Show your work.** | Any question flips to show how it was made: the first draft, what changed it, and each candidate's quote. |

## Revisions

**Nov 3, 2026, heuristic 8.** It read: "Spend attention where it changes the outcome. The next question is the one that best separates candidates who are still close. The quiz ends when every race is settled." That narrowed the field toward one winner, which is steering, and "Inform, don't steer" outranks "Start simple, go deep on demand." The California governor primary also showed narrowing couldn't be done honestly: across 34 candidates with stated positions, 83% of pairs shared no policy question at all. The quiz now ends with a map of where you match each candidate and why, not a verdict.

**Nov 3, 2026, heuristics 3, 6, 8 and 9: core and deep dive.** In a 61-candidate primary, keeping question counts equal (the old heuristic 6) would have meant 60+ questions, most about one candidate each. In a 5-candidate race, the same single-candidate questions add real nuance. Instead of a fixed rule for either, every voter gets the same short core picked from the field, then a deep dive shaped by their own answers. Two guardrails keep that adaptive part from steering: anyone the evidence can't rule out stays in contention, and questions about candidates the voter knows least about break the loop of only learning more about the leaders. Results now show how much evidence each match rests on, so a candidate with 2 stated positions can't look like a stronger match than one with 10.

**Nov 3, 2026, heuristic 3: specific answers broad, never the reverse.** The first California question pool had 123 statements, 83 of them about a single candidate, and only 5 with candidates on opposite sides. The rule that a position must answer "the same specific policy question" was hiding real disagreements: one candidate would eliminate property taxes and another would raise them on commercial property, which are opposite sides of whether property taxes should be lower. A specific proposal now counts toward a broader debate it clearly takes a side on, never the reverse, and a roll-up pass writes those broader debates. Emphasis (what a candidate talks about most) is never read as a stance on what they didn't mention.
