# Claude harness: OAuth token vs API key

Tend's Claude harness accepts either `CLAUDE_CODE_OAUTH_TOKEN` (a
`claude setup-token` OAuth token, tied to a Claude subscription) or
`ANTHROPIC_API_KEY` (a Console API key). The install skill recommends OAuth
for adopters who already have a Pro/Max/Team/Enterprise subscription, and
API key otherwise.

## What changes on June 15, 2026

Anthropic's canonical statement is in the Claude Code
[authentication page](https://code.claude.com/docs/en/authentication),
under "Generate a long-lived token":

> Starting June 15, 2026, Agent SDK and `claude -p` usage on subscription
> plans will draw from a new monthly Agent SDK credit, separate from your
> interactive usage limits.

Mechanics are in the support article
[Use the Claude Agent SDK with your Claude plan](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan):

> Starting June 15, 2026, Claude Agent SDK and `claude -p` usage no longer
> counts toward your Claude plan's usage limits.

> Available on Pro, Max, Team, and Enterprise plans starting on June 15, 2026.

> When your monthly credit runs out, additional Agent SDK usage flows to
> extra usage at standard API rates — but only if you've enabled extra
> usage. If extra usage isn't enabled, Agent SDK requests stop until your
> credit refreshes.

Plan allowances: Pro $20, Max 5x $100, Max 20x $200, Team Standard $20,
Team Premium $100, Enterprise usage-based $20, Enterprise seat-based
Premium seats $200. Seat-based Enterprise Standard seats are not
eligible. Anthropic emails eligible users instructions to claim the
credit before June 15; the claim is a one-time opt-in through the user's
Claude account, after which the credit refreshes automatically each
billing cycle.

`claude-code-action` runs Claude Code non-interactively under `claude -p`,
so tend workflows fall under this regime.

OAuth tokens themselves keep authenticating after June 15 — only the usage
bucket changes.

## Recommendation

**OAuth token, if you have an eligible Claude subscription.** The Agent
SDK credit is bundled with eligible plans you're already paying for;
using OAuth puts that allowance to work instead of billing tend runs
separately. For typical bot volume ($20/Pro is a few million Sonnet
tokens), the credit covers ordinary operation. Enable "extra usage" in
the Console so credit exhaustion overflows to API rates instead of
hard-stopping CI. Seat-based Enterprise Standard seats are not
eligible — admins on those seats should take the API-key path.

**API key, otherwise.** Use this when there's no subscription to draw on,
when the bot should bill against a dedicated Console org for accounting
reasons, or when the adopter wants per-key revocation rather than
account-level scoping.

## Trade-offs to weigh either way

- **Credit-exhaustion failure mode.** OAuth on a plan without "extra usage"
  enabled hard-stops when the credit runs out. API keys behave the same
  way if the Console org is funded by prepaid credits or capped by spend
  limits; postpay (card/invoice) keeps running until the cap or the run.
  Pick the configuration that matches your monitoring habits.
- **Leak blast radius.** A leaked OAuth token grants Agent SDK access for
  the whole subscription account; rotating it logs the bot out everywhere
  that account is authenticated. A leaked API key is a single revocable
  Console key. Tend's primary security boundary is the merge restriction
  (see [docs/security-model.md](security-model.md)), so this is secondary,
  but parallel to the Codex `auth.json` argument in the same doc.
- **Claim step.** The Agent SDK credit must be claimed once through the
  user's Claude account (Anthropic emails instructions before June 15);
  after that it refreshes automatically each billing cycle. Trivial, but
  a non-zero one-time setup step.

## What the install skill says

- Kickoff: introduces the choice, names the June 15, 2026 change, and
  surfaces the subscription-vs-no-subscription split.
- Step 7a: presents the two paths with the rationale summarized here, and
  links back to this doc.
