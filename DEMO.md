# Demo runbook

For driving LogAi live during *"The Work Your Team Is Never Going to Do."*
Total stage time: about 6 minutes.

---

## Before you leave for the venue

```bash
cd LogAi
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Pull the model you intend to demo with, over a network you trust
ollama pull qwen2.5:14b

# Rehearse both paths
LOGAI_PROVIDER=ollama  python run.py     # the real thing
LOGAI_PROVIDER=offline python run.py     # the fallback
```

Confirm the fallback works on battery, with wifi switched off. If Ollama fails on
stage, `LOGAI_PROVIDER=offline` is a two-word recovery and the audience will not
know the difference unless you tell them — and you should tell them, because
"here's the fixture I built in case the wifi died" is a better moment than a
flawless demo.

**Have a screen recording of a successful run on the machine.** If the laptop
refuses to cooperate entirely, play the recording and keep talking. Never debug
in front of a room.

---

## Setup, 60 seconds before you start

Two terminals and a browser, all pre-launched and pre-sized:

```bash
# Terminal 1 — the server
rm -f logai.db*                      # clean slate, so counts start at zero
LOGAI_PROVIDER=ollama python run.py

# Terminal 2 — ready but NOT run yet
python tools/generate_logs.py --count 600
```

Browser at `http://localhost:8080`, zoomed to about 125% for projector legibility.
Dashboard should read `0 events`.

---

## The run

**1 — The empty board.** "This is a syslog server. Nothing has been sent to it yet."
Point out the two panes: ranked issues on the left, raw stream on the right.

**2 — Fire the traffic.** Run terminal 2. Six hundred events in under a second.
Let the right-hand pane scroll. Say nothing for three or four seconds and let the
room watch the noise. That silence is the whole argument for the session.

> "That is a fleet of twelve devices for about ninety seconds. This is the thing
> your team is supposed to be reading."

**3 — The collapse.** Point at the header. 600-odd events, eighteen issues.

> "Nothing intelligent has happened yet. That is regex and a hash — forty lines
> in `fingerprint.py`. No GPU has been involved at any point so far."

**4 — The ranking.** Failed power supply at the top with roughly thirty events.
Scroll down to routine SSH logins with eighty-plus events, ranked well below it.

> "Volume did not decide this. A failed PSU on three chassis outranks eighty-one
> successful logins, and I can show you exactly why."

**5 — Open the top issue.** The score bar is the money shot. Severity, volume,
burst, host spread, novelty — every segment traceable to a rule in `scoring.py`.

> "An auditor can read this. So can your CFO. There is no model in this number."

**6 — Run triage.** Click it. Five issues reviewed. Open the finding.

> "Now the model runs — and only against the five things the ranking already
> surfaced. Not the six hundred log lines. That is what makes this affordable
> and what makes a 14-billion-parameter model on one GPU enough."

**7 — The citations.** Point at `cited events: 628, 627, 626` and the highlighted
rows below it.

> "Every claim traces to a specific event ID. If it cannot cite, it ships marked
> unverified with confidence forced to low. That rule is in code, not in the prompt."

**8 — The close.** Point at the draft remediation.

> "It drafts. It does not execute. There is no code path from this panel to a
> device, and there is not going to be one — because everything on the right-hand
> side of this screen is attacker-reachable text being fed to a system that holds
> credentials."

---

## Recovery

| Problem | Fix |
|---|---|
| Ollama not responding | Restart with `LOGAI_PROVIDER=offline`, say so out loud |
| Port 5514 in use | `--port 5515` on the generator, `SYSLOG_UDP_PORT=5515` on the server |
| Dashboard empty after generating | Check terminal 1 for the listener line; confirm the generator's target port matches |
| Nothing renders at all | Play the screen recording |
| Someone asks about a specific number | Open `logai/scoring.py` on screen — the weights are twelve lines and answering from source wins the room |

---

## Questions you will get

**"Isn't this just Zabbix?"** For the known signals, yes, and I would rather you
spent the money on monitoring coverage. This is the layer above: joining sources
that disagree, reading text with no metric behind it, and writing the artifact
at the end.

**"What model is that?"** Whatever the env var says. Swapping Ollama for Anthropic
is one line — that is deliberate, because the model you deploy today is obsolete
in nine months and it should never be a code change.

**"Would you run this in production?"** Not as it stands. No auth, SQLite, and
syslog is unauthenticated by design. It is a reference architecture, and the
limitations section of the README is honest about all of it.

**"How long did this take?"** Long enough to find two real bugs in the
fingerprinting — device WWNs were fragmenting one storage issue into three
separate alerts. That bug is the entire reason the deterministic layer needs to
be readable rather than clever.
