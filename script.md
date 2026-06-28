# "I Asked AI to Design 12 Circuit Boards. It Told Me Not To."

> YouTube script — maker-friendly, ~10 min.
> Spine: *one command, 12 boards.* Thesis: *the skill is knowing where AI does
> NOT belong.* Scripts do the mechanical 95%; an LLM does the one judgement step
> (reading the atomic14 module pages into each board's `board.yaml`).

---

## 1 · COLD OPEN — 0:00

**[SCREEN: black terminal, big font. You type `uv run python scripts/make.py`, hit enter. Hold on the first log lines.]**

**VO:** When I started this project, I did exactly what everyone on the internet is telling you to do right now. I asked an AI to spin up a *team of agents* to design a set of circuit boards for me.

**[CUT TO: you, to camera.]**

**TO CAMERA:** And it told me no. Claude pushed back. It said — this is a *mechanical* job. You don't want a swarm of agents improvising this. You want scripts. So I let it write the scripts instead.

**[CUT TO: the terminal time-lapse — boards rendering one after another — then a grid of all 12 finished boards rotating in 3D.]**

**VO:** That was the right call. Twelve ESP32 dev boards. One command. And the reason it actually works is the thing nobody wants to admit about AI right now.

**[TITLE CARD.]**

---

## 2 · THE PROBLEM — ~0:45

**[SCREEN: the Espressif module line-up — S2, S3, C3, C5, C6, H2 — photos or the module pages.]**

**VO:** Here's the job. Espressif make about a dozen modules with a PCB antenna and native USB. And for every single one of them, I wanted the same thing: a basic dev board. USB-C in, a 3-volt-3 regulator, a reset button, a boot button, a couple of LEDs, and all the pins broken out to headers.

**[SCREEN: a hand-drawn or animated dev board, parts highlighting as you name them.]**

**VO:** Now — that's the *same boring board* twelve times. And boring, repetitive work is exactly where humans make mistakes. Wire the LED to the wrong pin on board number nine at midnight and you won't find out until the thing won't boot.

**TO CAMERA:** This is the kind of job you *should* automate. The question is *how*.

---

## 3 · THE PRINCIPLE — ~2:00

**[SCREEN: split screen. Left: a workshop jig. Right: a person reading a datasheet.]**

**VO:** There are two completely different kinds of tool here, and the whole project comes down to telling them apart.

**VO:** The first is a *jig*. Same input, same output, every single time. Free, instant, perfectly repeatable. A script is a jig. I can run it today, run it next year, and get byte-for-byte identical boards.

**VO:** The second is more like an *apprentice* you send off to read the datasheet and come back with notes. Slower. Costs you something every time. And it might get it slightly wrong. That's a language model.

**TO CAMERA:** An AI in the middle of my routing loop would be slow, it'd cost money on every run, and it might *quietly* wire something wrong and never tell me. A script doesn't improvise. So I decided the AI gets to touch *one* file — the one file that actually needs a brain. Everything else is jigs.

---

## 4 · THE REVEAL — ~3:30

**[SCREEN: the terminal, run `make.py` for real. Let the stages scroll.]**

**VO:** So here's the one command. And all it's really doing is four steps, in order. Build the schematic. Place the parts on the board. Route the copper. And render a picture so I can check it.

**[SCREEN: open one generated board in KiCad, spin the 3D view.]**

**VO:** No mouse. No dragging traces. And that's not a mock-up — that's a real, manufacturable board I could send off and get back in the post.

---

## 5 · HOW IT WORKS — THE LEGO BIT — ~4:45

**[SCREEN: the bare skeleton board (USB-C, regulator, buttons, LEDs) on its own.]**

**VO:** The trick is that I'm not designing twelve boards. I'm designing *one* — a skeleton with all the stuff every board needs. The USB-C, the power, the buttons, the LEDs.

**[SCREEN: same skeleton, drop in three different modules + header strips.]**

**VO:** Then the generator snaps in the module and two header strips, and wires it all up automatically.

**[SCREEN: two skeletons side by side — mirror images. Highlight the reset button on one side, the boot button on the other; then flip.]**

**VO:** And here's a detail I really like. On some of these modules the **reset pin and the boot pin swap sides** — what's on the left of one module is on the right of another. So if I used a single fixed layout, half the boards would end up with long, ugly traces snaking across to reach a button.

**VO:** So I keep *two* versions of the skeleton — mirror images of each other. On one, the reset button's on the left and boot's on the right; on the other, they're flipped. The generator looks at which side those pins come out on and picks the matching baseboard, so the buttons always sit right next to their pins. A human would just eyeball that. The script makes the same call, the same way, every time.

---

## 6 · WHERE THE AI ACTUALLY LIVES — ~6:15

**[SCREEN: open a single `board.yaml`, highlight the `boot` field.]**

**VO:** So where's the AI in all this? Right here. This little file. It's the *only* thing on each board I write by hand — or rather, the only thing the AI writes.

**VO:** Some decisions you genuinely can't script. Which pins are safe to expose, which are off-limits because they're wired to internal flash — and this one: the **boot pin**.

**VO:** When you power up an ESP32, one special pin decides what it does — boot normally from flash, or drop into programming mode so you can upload new code. That's what the boot button on every dev board is for. But *which* GPIO does that job **changes depending on the chip** — it's one pin on the older Xtensa chips, a different one on most of the RISC-V chips, and different again on the C5.

**[SCREEN: your atomic14.com/esp32 module page.]**

**VO:** And all of that — which pin, on which chip — is written in plain English on my website, for humans. So that's the job I hand to the AI: read the page for this module, and tell me which pin is the boot pin. Messy human writing in, clean data out. That is the one thing a script can't do and a language model can.

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

**VO:** Now let me be honest about where this got hard. The USB data lines — D-plus and D-minus — are a *differential pair*. Best practice is to route them together, matched, side by side. So that's what I told the router to do on every board.

**[SCREEN: a board where they fell back to single-ended.]**

**VO:** And on most boards, it nailed it. But on a few of the tighter, more crowded ones, the autorouter just *couldn't* complete the pair — there wasn't room to keep them together all the way. So rather than fail, it falls back and routes them individually.

**TO CAMERA:** Is that perfect? No. A pro doing USB at high speed would care. For a basic dev board at USB full-speed, it's absolutely fine. But I'm not going to stand here and pretend the automation won every fight — it didn't, and it tells you when it compromised.

---

## 9 · HONESTY BEAT 2 — THE GREEN CHECK THAT LIED — ~9:00

**[SCREEN: the electrical check passing — a green result.]**

**VO:** One more, because this one taught me a lesson. Early on, the automated electrical check came back completely clean. Green across the board.

**[SCREEN: the render showing the USB pins unconnected.]**

**VO:** And the board was *dead*. The USB pins weren't actually connected to anything. The checker was happy. The board was useless.

**TO CAMERA:** So now, every board gets a picture rendered at the end — and I *look* at it. That's the real lesson here. Automation gets you ninety-five percent of the way. The last five percent is still you, looking at the thing with your own eyes.

---

## 10 · CLOSE — ~9:45

**[SCREEN: the grid of 12 boards again.]**

**VO:** So — twelve boards, one command. Scripts do the boring, mechanical ninety-five percent. The AI does the one bit that needs judgement: reading the docs and writing the notes. And a set of guardrails makes sure it can't hurt itself.

**TO CAMERA:** The lesson I actually want you to take away isn't "use AI." It's the opposite. The clever part was working out where *not* to. Even the AI knew that before I did.

**[SCREEN: repo link / atomic14 lower-third.]**

**TO CAMERA:** Everything's on GitHub, link below. And there's a whole other video in *how* the AI fills in that one file from the website — tell me in the comments if you want that one. Cheers.

**[END CARD.]**

---

## Shot list (film while it's fresh)

- The `make.py` time-lapse — the money shot. Film it clean.
- The LED safety hard-error firing live (beat 7).
- USB diff-pair vs single-ended fallback, two boards side by side (beat 8).
- The "green check that lied" — passing ERC next to the render with unconnected USB pins (beat 9).
- One `board.yaml` on screen — the only hand-authored file (beats 6).
- Two mirrored baseboards, reset/boot buttons swapping sides (beat 5).

## Accuracy notes (don't contradict the code on camera)

- **Baseboard mirror (beat 5)** = *physical layout*. The EN (reset) and BOOT pins
  swap sides as a pair between module families; the generator keys the choice off
  the EN pin's edge (`build_board.py: baseline_dir`) and picks `baseline-left-en`
  or `baseline-right-en`. No GPIO numbers here.
- **Boot pin (beat 6)** = *logical*. The `board.yaml` `boot` field — which GPIO
  the boot button pulls low for programming-vs-flash. GPIO0 on Xtensa (S2/S3),
  GPIO9 on most RISC-V (C3/C6/H2), GPIO28 on C5. This does NOT affect baseboard
  selection.
- The diff-pair fallback is real: routing defaults to diff-pair with a
  single-ended fallback (`route_all.py`, `--no-diff` forces single-ended).
