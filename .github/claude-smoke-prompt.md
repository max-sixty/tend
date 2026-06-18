You are running a smoke test of the headless Claude harness.
Do exactly the following and then stop, without asking any
questions:

1. Use the Bash tool to run: `pwd && date -u +%FT%TZ && uname -a`.
2. Use the Bash tool to run: `gh api user --jq .login`. This goes
   through the credential-injecting proxy — it should print the bot
   account's login even though your environment only holds a dummy
   token.
3. Use the Bash tool to run: `git ls-remote origin HEAD`. This must
   succeed (not fatal): it proves the persisted-credential strip
   didn't break git and that git reaches origin through the proxy.
4. Use the Bash tool to run this exact script as ONE invocation.
   Claude Code strips its own auth variables from tool subprocesses,
   so these normally read `unset`; the check fails only if one holds
   a NON-EMPTY value lacking the tendproxydummy marker — a real
   secret reachable by the agent. unset / empty / dummy all mean
   isolated:

   v=isolated
   for n in CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_API_KEY; do
     if ! val=$(printenv "$n"); then echo "envstate $n=unset"; continue; fi
     if [ -z "$val" ]; then echo "envstate $n=empty"; continue; fi
     if printf '%s' "$val" | grep -q tendproxydummy; then echo "envstate $n=dummy"
     else echo "envstate $n=OTHER"; v=REAL-LEAK; fi
   done
   echo "anthropic-cred: $v"

   It must end with the isolated verdict (not REAL-LEAK). That this
   turn runs and completes at all is the proof the proxy injected
   the real secret for inference — Claude authenticated to
   api.anthropic.com holding only a dummy.
5. Reply with one line in the form:
   "Smoke test ran at <date>; gh authenticated as <login>; git ls-remote ok; <the anthropic-cred output line>".

Do not push, comment, or modify files. Use only the Bash tool.
