# "I Asked AI to Design 12 Circuit Boards. It Told Me Not To."

> YouTube script — maker-friendly, ~10 min.
> Spine: *one command, 12 boards.* Thesis: *the skill is knowing where AI does
> NOT belong.* Scripts do the mechanical 95%; an LLM does the one judgement step
> (reading the atomic14 module pages into each board's `board.yaml`).

---

## 1 · COLD OPEN — 0:00

**[SCREEN: the actual Claude conversation — the "team of agents" ask and the refusal. This is the money shot for the title.]**

I wanted a dev board for every single ESP32 module. All twelve of them.

So I did what everyone on the internet is telling you to do right now — I asked Claude to spin up a *team of AI agents* to design the boards for me.

And it said: "No."

It was very nice about it, but it basically said: "You're wasting my time and your own — this is not an AI problem. You don't want a swarm of agents randomly wiring things up. You want some deterministic scripts."

So I let it write those instead.

**[SCREEN: black terminal, big font. Type `uv run python scripts/make.py --all`, hit enter. Time-lapse of boards rendering one after another — then a grid of all 12 finished boards rotating in 3D (the hero montage).]**

And there we go. Twelve fully routed dev boards, from one command. Let me show you why the AI was right to turn the job down.

---

## 2 · THE PROBLEM — ~0:45

**[SCREEN: a dev board close-up, parts highlighting as you name them.]**

I've built a few Espressif dev boards, and they always end up pretty similar.

You have the USB-C port with its 5.1K resistors.

You have the 3.3 volt regulator along with a bunch of decoupling capacitors.

Two buttons — one to control boot mode and another to reset the module.

Some LEDs.

And of course the pin headers to break out the GPIO pins.

**[SCREEN: the Espressif module line-up — S2, S3, C3, C5, C6, H2 — photos or the module pages.]**

**VO:** Espressif make about a dozen modules with a PCB antenna and native USB, and I wanted that same board for every one of them. The only things that really differ are the number of pins on the headers, the size of the module, the positions of everything — and the actual routing of the copper.

**VO:** So that's the *same boring board* twelve times. And boring, repetitive work is exactly where humans make mistakes. Wire the LED to the wrong pin on board number nine at midnight and you won't find out until the thing won't boot.

**TO CAMERA:** This is the kind of job you *should* automate. The question is *how*.

---

## 3 · THE PRINCIPLE — ~2:00

**[SCREEN: split screen. Left: a workshop jig. Right: a person reading a datasheet.]**

**VO:** There are two completely different kinds of tool here, and the whole project comes down to telling them apart.

**VO:** The first is a *jig*. Same input, same output, every single time. Free, instant, perfectly repeatable. A script is a jig. I can run it, wipe everything, and run it again — and get byte-for-byte identical boards.

**VO:** The second is more like an *apprentice* you send off to read the datasheet and come back with notes. Slower. Costs you something every time. And it might get it slightly wrong. That's a language model.

**TO CAMERA:** Now imagine putting an AI in the middle of that loop. Every run gets slower. Every run costs money. And worst of all — one day it'll wire something wrong, *quietly*, and I'll never know. A script can't do that. It's too boring to make mistakes — it does exactly the same thing, every single time. So here's the deal I settled on: the AI gets *one* file per board — the one file that actually needs a brain. Everything else is a jig.

---

## 4 · THE REVEAL — ~3:30

**[SCREEN: the terminal, run `make.py` for real. Let the stages scroll.]**

**VO:** So here's the one command. All it's really doing is a handful of steps, in order. Build the schematic. Place the parts on the board. Route the copper. Render a picture so I can check it. And zip up the files the factory needs.

**[SCREEN: open one generated board in KiCad, spin the 3D view. Zoom the ground pour and the fat power traces.]**

**VO:** No mouse. No dragging traces. We've got ground pours, thick 0.4 millimetre tracks for power, and the USB data lines routed as a differential pair. That's not a mock-up — it's a real, manufacturable board I could send off and get back in the post.

---

## 5 · HOW IT WORKS — THE LEGO BIT — ~4:45

**[SCREEN: the bare skeleton board (USB-C, regulator, buttons, LEDs) on its own.]**

**VO:** The trick is that I'm not designing twelve boards. I'm designing *one* — a skeleton with all the stuff every board needs. The USB-C, the power, the buttons, the LEDs.

**[SCREEN: same skeleton, drop in three different modules + header strips.]**

**VO:** Then the generator snaps in the module and two header strips, draws the board outline to fit, and wires it all up with net labels. Automatically.

**[SCREEN: two skeletons side by side — mirror images. Highlight the reset button on one side, the boot button on the other; then flip.]**

**VO:** And here's a detail I really like. On some of these modules the **reset pin and the boot pin swap sides** — what's on the left of one module is on the right of another. Use a single fixed layout and half the boards end up with long, ugly traces snaking across to reach a button.

**VO:** So I keep *two* versions of the skeleton — mirror images of each other. The generator looks at which side the reset pin comes out on and picks the matching baseboard, so the buttons always sit right next to their pins. A human would just eyeball that. The script makes the same call, the same way, every time.

**[SCREEN: flip a board over — zoom the vertical silk text down the middle of the back: "ESP32-S3-MINI-1 v1.0".]**

**VO:** And the jig signs its work. Every board gets the module name and the exact git revision that generated it, printed down the back in silkscreen. If one of these turns up in a drawer in five years, the board itself tells you precisely which version of the scripts made it.

---

## 6 · WHERE THE AI ACTUALLY LIVES — ~6:15

**[SCREEN: open a single `board.yaml`, highlight the `boot` field.]**

**VO:** So where's the AI in all this? Right here. This little file. It's the *only* thing on each board that's written by hand — or rather, the only thing the AI writes.

**VO:** Some decisions you genuinely can't script. Which pins are safe to break out, and which are off-limits because they're wired to the module's internal flash. Which pins the chip reads at power-up, so the on-board LED mustn't sit on them. And this one: the **boot pin**.

**VO:** When you power up an ESP32, one special pin decides what it does — boot normally from flash, or drop into programming mode so you can upload new code. That's what the boot button on every dev board is for. But *which* GPIO does that job **changes depending on the chip** — it's one pin on the older Xtensa chips, a different one on most of the RISC-V chips, and different again on the C5.

**[SCREEN: your atomic14.com/esp32 module page.]**

**VO:** And all of that — which pin, on which chip, which pins to leave alone — is written in plain English on my Espressif module information site, for humans. So that's the job I hand to the AI: read the page for this module, and fill in this file. Messy human writing in, clean data out. That is the one thing a script can't do and a language model can.

**TO CAMERA:** That's the whole philosophy. The AI proposes. The jig verifies.

---

## 7 · GUARDRAILS — "AI PROPOSES, THE JIG VERIFIES" — ~7:30

**[SCREEN: deliberately set the builtin LED to a strapping pin, run the build, show the hard error.]**

**VO:** Watch this. The on-board LED can't go on just *any* pin. Some pins are "strapping" pins — the chip reads them at power-up to decide how to boot. Put your LED on one of those and the board won't flash properly.

**[SCREEN: the build aborts with the error.]**

**VO:** So if the AI suggests a bad pin, the build just... refuses. It won't generate the board. The robot stops me making the mistake. That's the safety net under the AI — and it's why I'm comfortable letting it near this at all.

---

## 8 · HONESTY BEAT 1 — THE LOG THAT NEARLY FOOLED ME — ~8:15

**[SCREEN: zoom in on the USB D+/D- traces — two tracks running perfectly parallel from the USB-C up to the module.]**

**VO:** Now let me be honest about where this got hard: the routing. I found a really nice tool for this — KiCadRoutingTools. You can drive it from inside KiCad as a plugin, or just call its scripts, which is exactly what a jig wants. Claude and I spent a while getting it dialled in across all twelve boards.

**VO:** The USB data lines — D-plus and D-minus — are a *differential pair*. Best practice is to route them together, matched, side by side. And look — that's exactly what it does. On every one of the twelve boards, those two tracks run coupled, fifteen hundredths of a millimetre apart, all the way from the connector to the module.

**[SCREEN: the router's log — "deferred to single-ended ['D+', 'D-']" highlighted. Big red circle.]**

**VO:** But here's the bit that nearly caught me out. When I read the router's log, I found *this* — "deferred to single-ended, D-plus, D-minus". I read that and thought the pair had failed. I was halfway through writing "the automation lost this fight" before I went and actually *measured the copper*.

**VO:** The pair was fine. That message is about a couple of *millimetre-long* stubs at the connector itself — the USB-C has two pads for each data line, and the router is smart enough to know that coupling a one-millimetre pad bridge is meaningless, so it finishes those tiny legs individually. The main run — the bit that matters — is a textbook coupled pair on every single board.

**TO CAMERA:** So the automation was right, and *I* was the unreliable component — I trusted a log line instead of the board. Hold that thought, because it's about to become a theme.

---

## 9 · HONESTY BEAT 2 — THE GREEN CHECK THAT LIED — ~9:00

**[SCREEN: the electrical check passing — a green result.]**

**VO:** One more, because this one taught me a lesson. Early on, the automated electrical check came back completely clean. Green across the board.

**[SCREEN: the render showing the USB pins unconnected.]**

**VO:** And the board was *dead*. The USB pins weren't actually connected to anything. The checker was happy. The board was useless.

**TO CAMERA:** So now, every board gets a picture rendered at the end — and I *look* at it. That's the real lesson here. Automation gets you ninety-five percent of the way. The last five percent is still you, looking at the thing with your own eyes.

---

## 10 · CLOSE — ~9:45

**[SCREEN: the grid of 12 boards again, then the fab zips landing in the terminal.]**

**VO:** So — twelve boards, one command. Every one of them passes the design rule check with zero errors, and the Gerbers come out the other end zipped up and ready to send to the fab. Scripts do the boring, mechanical ninety-five percent. The AI does the one bit that needs judgement: reading the docs and writing the notes. And a set of guardrails makes sure it can't hurt anything.

**[SCREEN: the GitHub Actions run going green, then the release page with all the zips.]**

**VO:** In fact, it doesn't even need my computer any more. The whole pipeline runs on GitHub's servers — every change rebuilds all twelve boards from scratch inside a KiCad container, and tagging a release publishes the Gerbers and the KiCad projects automatically. You don't have to run anything: the files you'd send to the fab are sitting on the releases page right now.

**TO CAMERA:** The lesson I actually want you to take away isn't "use AI." It's the opposite. The clever part was working out where *not* to. Even the AI knew that before I did.

**[SCREEN: repo link / atomic14 lower-third.]**

**TO CAMERA:** So — over to you. Which of these boards should we actually get manufactured? What would you like to see? Tell me in the comments. Everything's on GitHub, link below — and there's a whole other video in *how* the AI fills in that one file from the website, so let me know if you want that one too. Cheers.

**[END CARD.]**

---

## Shot list (film while it's fresh)

- The Claude "no" conversation for the cold open — screenshot or screen recording.
- The `make.py --all` time-lapse — the money shot. Film it clean.
- The hero montage (`build/montage_hero.png`) and the to-scale montages
  (`montage_top_scale.png` / `montage_bottom_scale.png`) for the grid shots —
  the to-scale ones make the size differences land.
- Component close-ups for beat 2 (USB-C, regulator, buttons, LEDs, headers).
- Two mirrored baseboards, reset/boot buttons swapping sides (beat 5).
- The back-silk version stamp (beat 5) — flip to the bottom view, zoom the
  vertical "ESP32-S3-MINI-1 v1.0" text between the pin-label columns.
- One `board.yaml` on screen — the only hand-authored file (beat 6).
- The LED safety hard-error firing live (beat 7).
- Beat 8 needs BOTH shots: the coupled D+/D- pair zoomed on the board (two
  parallel tracks, USB-C to module) AND the misleading log line `deferred to
  single-ended ['D+', 'D-']` (run `scripts/lib/route_board.py` on a freshly
  built board to capture it live).
- The "green check that lied" — passing ERC next to the render with unconnected USB pins (beat 9).
- Ground pour + 0.4mm power traces zoom (beat 4).
- GitHub Actions run going green + the v1.0 release page with 24 zips (close).

## Accuracy notes (don't contradict the code on camera)

- **Baseboard mirror (beat 5)** = *physical layout*. The EN (reset) and BOOT pins
  swap sides as a pair between module families; the generator keys the choice off
  the **EN pin's** edge (`build_board.py: baseline_dir`) and picks `baseline-left-en`
  or `baseline-right-en`. No GPIO numbers here.
- **Boot pin (beat 6)** = *logical*. The `board.yaml` `boot` field — which GPIO
  the boot button pulls low for programming-vs-flash. GPIO0 on Xtensa (S2/S3),
  GPIO9 on most RISC-V (C3/C6/H2), GPIO28 on C5. This does NOT affect baseboard
  selection.
- **USB speed (beat 8)**: ESP32 native USB is **full-speed (12 Mbps)** — never say
  "high speed" on camera; the point is that full-speed is *forgiving* of an
  unmatched pair, not that high speed is.
- **Diff pair (beat 8) — measured on the copper, 2026-07-12**: ALL 12 boards
  route D+/D- as a proper coupled pair — 92–96% of the D+ track length runs
  at 0.30 mm centre-to-centre (0.15 track + 0.15 gap) from D- on the same
  layer. The log line `deferred to single-ended ['D+', 'D-']` refers ONLY to
  the millimetre-scale legs at the USB-C's duplicated pads (A6/B6, A7/B7 —
  multi-point nets); the router couples the long leg and finishes the short
  pad bridges single-ended. Do NOT say the pair failed, fell back, or was
  declined — the beat is now "I misread the log; the copper was right."
  (Verify script: measure D+ segments' distance to same-layer D- segments in
  the .kicad_pcb — see the coupling check used on 2026-07-12.)
- **Zero errors (beat 10)** = *DRC on the PCB*. Don't claim zero **ERC**
  violations — the skeleton ships with ~58 pre-existing ones by design; the gate
  is "no *new* errors."
- The router is **KiCadRoutingTools by drandyhaas** (github.com/drandyhaas/
  KiCadRoutingTools) — credit him on camera. We run a pinned fork
  (atomic14/KiCadRoutingTools, `dev-board-fixes`) that adds one small feature:
  keeping vias out of same-net SMD pads (`--same-net-pad-clearance`).
- **The one command (beats 1/4/10)**: plain `make.py` only builds + routes.
  Renders and fab zips need `--render`/`--fab` — on camera, type
  **`make.py --all`** so the montage + zips shown actually come from that run.
- **"Byte-for-byte identical" (beat 3)**: true at a given commit (UUIDs are
  deterministic) — but the back silk bakes in `git describe`, so boards from
  different commits differ by exactly that stamp. Say "run it twice, get the
  same boards"; avoid "next year" unless you add "from the same commit".
- **Back-silk stamp (beat 5)**: text is `<module> <git describe>` — e.g.
  `ESP32-S3-MINI-1 v1.0` from the tag, `...v1.0-2-gc9c8c5d` between tags,
  `-dirty` with uncommitted changes. Film a board built from the v1.0 tag for
  a clean stamp.
- **CI (close)**: runs in the official `kicad/kicad:10.0-full` Docker image;
  a `v*` tag publishes the release (12 fab zips + 12 project zips + montages).
  Releases page: github.com/atomic14/kicad-esp32-dev-boards/releases.
