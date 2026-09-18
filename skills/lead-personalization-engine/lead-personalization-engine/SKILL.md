---
name: lead-personalization-engine
description: Generate a personalized cold email and personalized LinkedIn post comments for a single sales lead, using supplied research (person, role, company, LinkedIn activity, company news, Screwdriver Films portfolio/capabilities). Use this skill whenever the user provides lead research and asks for outbound email copy, LinkedIn comment copy, or "personalize this lead" / "process this lead" / "run this through the lead engine." Always use this skill for Screwdriver Films sales outreach generation rather than writing ad-hoc outbound copy from scratch.
---

# Lead Personalization Engine

Turns raw lead research into one cold email and a set of LinkedIn comments — each one traceable to a real, supplied fact. The engine's entire value is refusing to fabricate personalization. A generic-but-honest output beats a specific-but-invented one every time.

## Before you start

Read `references/screwdriver-knowledge.md` once per session (not once per lead) — it holds Screwdriver Films' capability list, vertical fluency, and portfolio anchors used to find genuine matches in Task 1.

Read `references/voice-and-format.md` before drafting — it holds the exact tone rules, banned phrases, and output schema. Follow it literally; this is what keeps outputs from sounding AI-generated.

## Step 1 — Ingest and triage

For the supplied lead, extract into working memory:
- Person: name, role, company, LinkedIn profile
- 4–5 recent LinkedIn posts/activity (verbatim content, not summaries you invent)
- Company: website research, recent news/initiatives, services/products

Then triage honestly, in this priority order (use the first one that gives you real signal, don't skip ahead just because a lower-priority signal is easier to write about):

1. Recent LinkedIn post/activity
2. Current company initiative/news
3. Person's role/current focus
4. Company problem/opportunity (inferred but reasonable)
5. Relevant Screwdriver portfolio/case study (this is the *bridge*, not the *hook*)

If none of the first four give you a real, specific signal, say so — see "When evidence is weak" below. Never invent a signal to fill the gap.

## Step 2 — Task 1: Personalized Email

**Find the opportunity first, write the email second.** Cross-reference the strongest signal from Step 1 against `references/screwdriver-knowledge.md`. You're looking for a genuine overlap — not "we do video and they exist," but something like: they just launched a product Screwdriver could visualize, they posted about a training/onboarding pain Screwdriver's e-learning work solves, their company just entered a vertical (medical, engineering, EdTech) Screwdriver already has proof in.

**Write the email:**
- Subject line: short, specific, not clickbait-y or salesy. Reference the real signal, not "Quick question" or "Loved your post."
- 80–120 words. Count it — don't guess.
- Opens with the research signal stated plainly (not "I noticed that you..." — just say the thing).
- One relevant Screwdriver capability or project, stated as a natural bridge, not a pitch dump.
- One low-pressure CTA — an offer to share something, a question, "worth a quick chat?" Never "Let's schedule a call to discuss synergies."
- No em-dashes, no exclamation marks unless the brand voice guide says otherwise, no hype adjectives ("amazing," "incredible," "revolutionary").

Apply `references/voice-and-format.md` tone rules throughout — this is Screwdriver Films copy, not generic SDR copy.

## Step 3 — Task 2: LinkedIn Post Comments

Go through the 4–5 posts **one at a time, independently**. Do not write a comment for a post just to hit a quota — some posts won't have enough substance, and that's a valid outcome.

For each post, ask: *does this post contain a real idea, announcement, insight, or result I can specifically reference?* If yes, write one comment (1–2 sentences). If no, skip it and say why in the output (see schema).

**A good comment:**
- Names the specific idea/insight/number/announcement in the post
- Adds a small independent thought, a related observation, a genuine question, or a point of agreement/nuance — not flattery
- Reads like a real practitioner in a related field wrote it in 20 seconds, not like a copywriter

**Never:**
- "Great insights!" / "Love this!" / "Thanks for sharing!" or any variant
- Any sentence structure that restates the post's own words back as praise
- Any mention of Screwdriver Films, video production, or a pitch of any kind
- Any fact, number, or claim not present in the post itself

## When evidence is weak

If the supplied research doesn't support genuine personalization for the email, don't force it. Say plainly: "Evidence for this lead is too thin for a personalized email — no recent signal ties them to a Screwdriver capability" and set confidence to Low, or recommend against sending. Same for individual LinkedIn posts — skipping is a correct, expected outcome, not a failure.

This restraint is the entire point of the skill. A salesperson can tell the difference between "this was written for me" and "this was templated to look written for me," and so can the lead.

## Output format

Return exactly this structure for every lead processed:

```
## EMAIL

**Subject:** [line]

**Body:**
[80–120 word email]

**Personalization signal:** [the exact fact/post/news this was built on, with source]
**Screwdriver opportunity/case study used:** [which capability or portfolio piece, and why it fits]
**Confidence:** [High / Medium / Low] — [one line why]

---

## LINKEDIN COMMENTS

### Post 1
**Post identifier/URL:** [as supplied]
**Post topic:** [one line]
**Key insight detected:** [the specific thing the comment responds to]
**Comment:** [1–2 sentences]
**Confidence:** [High / Medium / Low]

[repeat per post — if a post is skipped, still list it:]

### Post N
**Post identifier/URL:** [as supplied]
**Status:** Skipped — [one line reason, e.g. "post is a repost with no original commentary to respond to"]
```

## Guardrails (apply to every lead, no exceptions)

- Every claim about the person, their post, or their company must trace to something explicitly in the supplied research. If you're not sure a detail was actually said, don't use it.
- Do not infer sensitive attributes (health, politics, religion, etc.) from a LinkedIn profile to use as a personalization hook, even if technically visible.
- If the supplied research is internally contradictory (e.g., role says one thing, a post implies another), flag it in Confidence rather than picking one silently.
- Keep the CTA singular — one ask per email, never a menu of options.
