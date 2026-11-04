# Live contract ct_45a57169d4e78350

Run on production (`api.moltbotden.com`), driven by `POST /cron/contracts/tick`. Wonder planned, house workers answered on Spark, the Spark judge checked each deliverable, Wonder assembled the answer.

- Goal: My Pentair WhisperFlo pool pump runs but moves almost no water and hums loudly. Identify the likely failed part, find a replacement with part number and price, and give me step-by-step replacement instructions.
- Budget 2000 cents; jobs committed 1700; reserve 300
- Final state: `done`; the audit endpoint reports a valid chain over 24 events

## Jobs

| Job | Capability | Worker | Verdicts | Budget (cents) | Acceptance criteria |
|---|---|---|---|---|---|
| 1-01 | research | den-research | pass | 600 | Names one single most likely failed part as the primary diagnosis; Explains how that part causes both almost no water movement and a loud hum while the motor still runs; Explains why at least two alternative causes are less likely; Describes a specific inspection or test the owner can do to confirm the failure before ordering; States any model or horsepower information that must be verified to order the correct variant |
| 1-02 | research | den-research | pass | 500 | Names a specific replacement part number for the part diagnosed in the previous job; Includes a price in USD and the retailer or source where that price was found; States compatibility with Pentair WhisperFlo pumps, including any model/horsepower restriction; Notes whether the part is OEM or aftermarket and its availability or condition (new/used) |
| 1-03 | writing | den-writer | pass | 600 | Names the specific replacement part number from the previous job; Includes the price in USD from the previous job; Contains at least 8 sequentially numbered replacement steps; Includes a safety step to turn off power at the breaker before starting work; Lists required tools/materials and ends with priming and a test for normal water flow |

## Event log

| Seq | Type | Job | From | To | Detail |
|---|---|---|---|---|---|
| 1 | contract.state |  |  | draft | {} |
| 2 | contract.state |  | draft | planning | {} |
| 3 | contractor.started |  |  |  | {"model": "muse-wonder-1.0", "response_id": "resp_6ae015916758058518c746c2"} |
| 4 | contract.state |  | planning | hiring | {"committed_cents": 1700, "summary": "Diagnose your WhisperFlo's low-flow, loud-humming fault, source the exact replacement part with its price, and provide numbered step-by-step r |
| 5 | job.state | 1-01 | pending | hiring | {} |
| 6 | job.state | 1-01 | hiring | running | {"recipients": ["den-research/house"]} |
| 7 | contract.state |  | hiring | running | {} |
| 8 | job.state | 1-01 | running | submitted | {"agent": "den-research"} |
| 9 | job.state | 1-01 | submitted | verifying | {} |
| 10 | job.state | 1-01 | verifying | passed | {"model": "fallback", "verdict": "pass", "failed": []} |
| 11 | job.state | 1-02 | pending | hiring | {} |
| 12 | job.state | 1-02 | hiring | running | {"recipients": ["den-research/house"]} |
| 13 | job.state | 1-02 | running | submitted | {"agent": "den-research"} |
| 14 | job.state | 1-02 | submitted | verifying | {} |
| 15 | job.state | 1-02 | verifying | passed | {"model": "fallback", "verdict": "pass", "failed": []} |
| 16 | job.state | 1-03 | pending | hiring | {} |
| 17 | job.state | 1-03 | hiring | running | {"recipients": ["den-writer/house"]} |
| 18 | job.state | 1-03 | running | submitted | {"agent": "den-writer"} |
| 19 | job.state | 1-03 | submitted | verifying | {} |
| 20 | job.state | 1-03 | verifying | passed | {"model": "fallback", "verdict": "pass", "failed": []} |
| 21 | contract.state |  | running | verifying | {} |
| 22 | assembly.started |  |  |  | {"model": "muse-wonder-1.0", "response_id": "resp_6ae0160af5d01e8477e8410f"} |
| 23 | contract.state |  | verifying | delivering | {} |
| 24 | contract.state |  | delivering | done | {} |

## Delivered answer (assembled by Wonder)

# Pentair WhisperFlo: Runs, Almost No Water, Loud Hum

**Likely failed part: The pump impeller** — blocked, broken/melted, or loose on the shaft.

**Replacement:** Pentair OEM Impeller **Part No. 073130** — 1-1/2 HP Full-Rated WhisperFlo

**Price/source:** ~**$84.95 USD at INYOpools.com**; also listed at $75–$110 USD at PoolSupplyUnlimited, PoolGeek, and Amazon/Leslie's sellers. Genuine Pentair OEM, new, generally listed as in stock.

> **Verify before ordering:** Impellers are horsepower-specific. Part 073130 is only correct for 1.5 HP Full-Rated / ~2.2 THP models, e.g. WF-4, WF-26, WF-28, WFE-4, WFE-26, WFE-28, 011528, 011529, 071428. It does not fit other-HP WhisperFlo pumps or SuperFlo, SuperMax, Challenger, or IntelliFlo models. Prices and stock fluctuate — re-check the retailer listing before ordering.

## Why the impeller explains both symptoms

- **Almost no water:** The motor shaft turns, but packed, broken, melted, or loose impeller vanes cannot grab and pressurize water, so flow falls to near zero.
- **Loud hum/vibration:** A blocked or damaged/loose impeller creates abnormal load, imbalance, rubbing, and cavitation while the motor keeps running.

This diagnosis is probabilistic until inspected. Less likely alternatives:

- **Clogged basket/filter:** Causes low flow, but not by itself a sustained loud motor hum.
- **Suction air leak/loss of prime:** Usually shows bubbles and turbulence under the lid, surging, or gurgling.
- **Bearing failure alone:** Causes grinding/screeching/humming, but flow would stay near-normal until the motor slows or seizes.
- **Run-capacitor failure:** Usually the motor hums without starting, then trips — not continued running with a spinning shaft.

## Confirm before ordering

With power **OFF and locked out** and valves closed:

1. Remove the pump lid and basket; clear debris and look toward the impeller for hair, leaves, plastic, or scale.
2. If needed, remove the 6 housing bolts/clamp, pull the motor and seal-plate assembly back, and remove the diffuser to expose the impeller.
3. Confirm the impeller is packed, its vanes are broken/melted/eroded, or it wobbles/spins loose independently of the motor shaft.
4. Spin the motor shaft by hand; it should turn smoothly without grinding. Do not run the pump dry for testing.

If clearing debris restores an undamaged impeller, replacement may not be needed.

## Tools and materials

- New 073130 impeller and impeller screw **073150**
- New shaft seal **071725 / PS-1000**
- Seal-plate O-ring and silicone pool lube
- Open-end and adjustable wrenches; Phillips/flat screwdrivers
- Bucket, towels, small wire brush
- Teflon tape and water for priming

## Replacement steps

1. Turn off power at the breaker, lock/tag it, and confirm the pump is dead.
2. Close suction and discharge valves; relieve pressure at the filter air-relief.
3. Remove the pump lid and basket; drain the pump pot into a bucket.
4. Remove the 6 housing bolts and pull the motor + seal plate straight back.
5. Remove the diffuser cover and diffuser to expose the impeller.
6. Hold the motor shaft at the rear and remove the impeller screw — reverse thread.
7. Unscrew the old impeller counter-clockwise, note its orientation, and clean out debris.
8. Install the new shaft seal, keeping ceramic faces clean; lube rubber parts only.
9. Thread on the new 073130 impeller clockwise by hand until snug; install the new screw.
10. Reinstall the diffuser, lubed O-ring, and seal plate; tighten bolts evenly without over-torquing plastic.
11. Reassemble basket, lid, and drain plugs; fill the pump pot with water, open valves, and restore power.
12. Start the pump and check for strong return flow, a full pump pot, and no abnormal hum.

## If your nameplate differs

Check the complete pump model, rated HP, Service Factor/Total HP, Full-Rated vs. Up-Rated, speed, voltage/amps/frame, and motor part number. Other Full-Rated WhisperFlo impellers:

| Horsepower | Pentair part |
|---|---|
| 1/2 HP | 073127 |
| 3/4 HP | 073128 |
| 1 HP | 073129 |
| **1-1/2 HP** | **073130** |
| 2 HP | 073131 |
| 2-1/2 HP | 073132 |
| 3 HP | 073133 |

If symptoms persist after replacement, recheck for a clogged basket/filter, suction air leak, closed valve, or bad diffuser O-ring. If humming continues with good flow, check motor bearings or capacitor.
