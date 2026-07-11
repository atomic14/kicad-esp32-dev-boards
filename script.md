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

**[SCREEN: black terminal, big font. Type `uv run python scripts/make.py`, hit enter. Time-lapse of boards rendering one after another — then a grid of all 12 finished boards rotating in 3D.]**

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

**VO:** The first is a *jig*. Same input, same output, every single time. Free, instant, perfectly repeatable. A script is a jig. I can run it today, run it next year, and get byte-for-byte identical boards.

**VO:** The second is more like an *apprentice* you send off to read the datasheet and come back with notes. Slower. Costs you something every time. And it might get it slightly wrong. That's a language model.

**TO CAMERA:** Now imagine putting an AI in the middle of that loop. Every run gets slower. Every run costs money. And worst of all — one day it'll wire something wrong, *quietly*, and I'll never know. A script can't do that. It's too boring to make mistakes — it does exactly the same thing, every single time. So here's the deal I settled on: the AI gets *one* file per board — the one file that actually needs a brain. Everything else is a jig.

---

## 4 · THE REVEAL — ~3:30

**[SCREEN: the terminal, run `make.py` for real. Let the stages scroll.]**

**VO:** So here's the one command. All it's really doing is four steps, in order. Build the schematic. Place the parts on the board. Route the copper. And render a picture so I can check it.

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

---

## 6 · WHERE THE AI ACTUALLY LIVES — ~6:15

**[SCREEN: open a single `board.yaml`, highlight the `boot` field.]**

**VO:** So where's the AI in all this? Right here. This little file. It's the *only* thing on each board that's written by hand — or rather, the only thing the AI writes.

**VO:** Some decisions you genuinely can't script. Which pins are safe to break out, and which are off-limits because they're wired to the module's internal flash. Which pins the USB D-plus and D-minus live on. And this one: the **boot pin**.

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

## 8 · HONESTY BEAT 1 — THE DIFF-PAIR COMPROMISE — ~8:15

**[SCREEN: zoom in on the USB D+/D- traces on a board, ideally a clean diff pair.]**

**VO:** Now let me be honest about where this got hard: the routing. I found a really nice tool for this — KiCadRoutingTools. You can drive it from inside KiCad as a plugin, or just call its scripts, which is exactly what a jig wants. Claude and I spent a while getting it dialled in across all twelve boards.

**VO:** The USB data lines — D-plus and D-minus — are a *differential pair*. Best practice is to route them together, matched, side by side. So that's what I told the router to do on every board.

**[SCREEN: a board where they fell back to single-ended.]**

**VO:** On most boards, it nailed it. But on a few of the tighter, more crowded ones, the autorouter just *couldn't* complete the pair — there wasn't room to keep them together all the way. So rather than fail, it falls back and routes them individually.

**TO CAMERA:** Does that matter? Honestly — not here. The ESP32's native USB is *full-speed*, twelve megabits. At that speed you can get away without a perfectly matched pair; it's just good practice to have one. But I'm not going to stand here and pretend the automation won every fight — it didn't, and it tells you when it compromised.

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

**TO CAMERA:** The lesson I actually want you to take away isn't "use AI." It's the opposite. The clever part was working out where *not* to. Even the AI knew that before I did.

**[SCREEN: repo link / atomic14 lower-third.]**

**TO CAMERA:** So — over to you. Which of these boards should we actually get manufactured? What would you like to see? Tell me in the comments. Everything's on GitHub, link below — and there's a whole other video in *how* the AI fills in that one file from the website, so let me know if you want that one too. Cheers.

**[END CARD.]**

---

## Shot list (film while it's fresh)

- The Claude "no" conversation for the cold open — screenshot or screen recording.
- The `make.py` time-lapse — the money shot. Film it clean.
- Component close-ups for beat 2 (USB-C, regulator, buttons, LEDs, headers).
- Two mirrored baseboards, reset/boot buttons swapping sides (beat 5).
- One `board.yaml` on screen — the only hand-authored file (beat 6).
- The LED safety hard-error firing live (beat 7).
- USB diff-pair vs single-ended fallback, two boards side by side (beat 8).
- The "green check that lied" — passing ERC next to the render with unconnected USB pins (beat 9).
- Ground pour + 0.4mm power traces zoom (beat 4).

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
- The diff-pair fallback is real: routing defaults to diff-pair with a
  single-ended fallback (`route_all.py`, `--no-diff` forces single-ended).
- **Zero errors (beat 10)** = *DRC on the PCB*. Don't claim zero **ERC**
  violations — the skeleton ships with ~58 pre-existing ones by design; the gate
  is "no *new* errors."
- The router is **KiCadRoutingTools** (sibling repo, Rust router) — confirm how
  you want to credit/name it on camera.
