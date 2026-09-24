# Data and operations: what the research found (September 2026)

After benchmark v0.2 showed that frontier models write correct small-system hydraulics from scratch, the owner chose a new direction on 2026-09-24. worldparts would supply what a model cannot write from scratch: real product data with provenance, and calibration and fault diagnosis against plant data. The owner added a hard constraint: if worldparts does not provide clear value, the project pivots or closes.

The same day, agents researched four questions, and skeptics independently checked their key claims. This note summarises what survived that check. **None of this is legal advice.**

## 1. Pump curve data: licences and sources

**The clean data is thin.**

- The [US DOE Compliance Certification Database](https://www.regulations.doe.gov/certification-data/CCMS-4-Pumps_-_General_Pumps.html) is public-domain US government data. DOE only [asks for acknowledgement](https://www.energy.gov/web-policies).
  - Checked on 2026-09-24: 27,908 rows, 7,612 basic models.
  - Each model has a single best-efficiency point: flow, head, PEI, speed, impeller diameter, and driver power at the rating points. The fields are set by [10 CFR 429.59](https://www.law.cornell.edu/cfr/text/10/429.59).
  - It has no head-flow curve and no NPSHr.
  - Data quality: 2,822 rows store efficiency as a fraction instead of percent, and brand names are inconsistent.
- Only a handful of permissively licensed open pump records exist:
  - the [Modelica Buildings library](https://simulationresearch.lbl.gov/modelica/releases/latest/help/Buildings_Fluid_Movers_Data_Pumps_Wilo.html) has 18 Wilo records, digitised with WebPlotDigitizer;
  - [AixLib](https://github.com/RWTH-EBC/AixLib) has about 30 polynomial records;
  - a [CC BY 4.0 solar-pump database](https://github.com/Muhriddin2301/dc-pump-aggregates-pv-database) has 22 digitised curves.

  None records a document revision or fit residuals. A BSD licence on these records covers only the contributors' own rights, not the manufacturers'.

**Full curves are public to read, but not to redistribute.**

- [EU Regulation 547/2012, Annex II](https://www.legislation.gov.uk/eur/2012/547/annex/II/adopted), item 7, requires performance curves with efficiency characteristics on manufacturers' free-access websites. This applies to end-suction, close-coupled, inline, vertical multistage and submersible water pumps. No format is required, and NPSH is not required.
- These curves are therefore public for any agent to read, including a from-scratch agent.
- Grundfos, Wilo, KSB, Xylem (Lowara, Goulds, Bell & Gossett), DAB, Franklin, Armstrong and Taco all forbid reproduction or redistribution of their site content without written permission, or allow only personal, non-commercial use.
- For Pedrollo and Calpeda no terms were found, so default copyright applies.

**The law is not settled in either direction.**

- A few digitised flow-head points are probably unprotected facts. In the US this follows from [Feist](https://supreme.justia.com/cases/federal/us/499/340/). In the EU, the [Database Directive](https://eur-lex.europa.eu/eli/dir/1996/9/oj/eng) lets lawful users extract insubstantial parts of a database (Art. 8) and voids contract terms that override this (Art. 15).
- However, in [Ryanair v PR Aviation](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:62014CJ0030) the CJEU held that where a database is not protected, the site's terms can still restrict reuse by contract.
- The risk grows with scale.

**Others are ahead.**

- PUMP-FLO says it is [licensed by over 150 pump manufacturers](https://pump-flo.com/). Manufacturer-funded curve catalogues are a mature channel.
- A third party built an agent skill in days that digitises and fits pump curves ([PumpCurveDigitizer](https://github.com/Accutant/PumpCurveDigitizer), oilfield pumps, no licence).
- The Hydraulic Institute's [HI 50.7 Electronic Data Exchange](https://www.pumps.org/resources/electronic-data-exchange/) is an existing XML format for pump technical data. A worldparts pack format should import it rather than compete with it.

**Conclusion.** An open corpus of manufacturer curves cannot be built cleanly without manufacturers' written permission, and it would not be a moat. The product-data pillar is on hold: the benchmark's data outcome (PIVOT-data) is excluded until redistribution rights exist.

## 2. Prior art for calibration and diagnosis

- **Calibration statistics are commodity.** [lmfit](https://lmfit.github.io/lmfit-py/) returns standard errors, correlations and reduced chi-square by default. Frontier models call scipy or lmfit unaided.
- **Identifiability and sensor worth** are covered by several tools:
  - [pyEMU](https://github.com/pypest/pyemu) and PEST++ (data worth);
  - Pyomo.DoE and parmest;
  - the [Fault Diagnosis Toolbox](https://github.com/ErikFrisk/fault-diagnosis-toolbox) (isolability, sensor placement);
  - [Chama](https://github.com/sandialabs/chama).
- **Leak diagnosis with hypothesis ranking and abstention** is covered at network scale. Open tools include LILA, nightflow and aquasentinel. Commercial tools include Bentley Darwin Calibrator, Xylem Vue, Siemens SIWA, TaKaDu and Aquasuite. [Mu et al. (2026)](https://arxiv.org/html/2608.18836) report an agentic system with evidence-gated abstention on BattLeDIM's L-Town network.
- **Agent interfaces to water models are becoming commodity:**
  - the [KWR](https://github.com/KWR-Water/epanet-mcp-server) and Eurecat EPANET MCP servers;
  - Qatium's MCP;
  - Bentley and Autodesk assistants;
  - [Agentic SWMM](https://github.com/Zhonghao1995/agentic-swmm-workflow/tree/main/mcp), whose MCP servers already include calibration (SCE-UA, DREAM-ZS) and sensitivity analysis.
- **Pump condition monitoring** is commercial and fleet-scale: Samotics SAM4, Augury, KSB Guard, Sulzer BLUE BOX and Grundfos monitoring products.
- **The one open gap found** is plant-scale diagnosis that ranks hypotheses across component types and states honestly when the data cannot decide. The component types are pump wear, pump speed, valve throttling, filter clogging, UV lamp decay and sensor faults.
  - [EPyT-Flow](https://epyt-flow.readthedocs.io/en/stable/tut.events.html) has no pump-degradation or filter-clogging events.
  - Open diagnosis codes are network-leak specific.
  - Caveat: a frontier model may be able to write this in one session. The operations benchmark tests exactly that.
- **Evidence on agents and calibration is mixed.**
  - A 2025 Water Research study found coding agents beat tool-calling agents on water-network calibration.
  - [HydroAgent](https://arxiv.org/abs/2605.17792) found that frontier LLMs calibrating hydrologic models scored NSE between -0.16 and 0.75.

## 3. BattLeDIM 2020 as a testbed

[BattLeDIM](https://battledim.ucy.ac.cy/) ([dataset, CC BY 4.0](https://zenodo.org/records/4017659)) is the right public testbed for network leak localisation, but not for this step:

- its ground truth has been public since 2020;
- the dataset is 550 MB;
- worldparts cannot load the 782-junction L-Town network (it has no INP import, demand junctions or PRVs);
- the winning methods are standard recipes with open code.

It stays a candidate for a later external-validity check on a WNTR backend. It is not part of the operations benchmark's decision gate.

## 4. What changed in the plan

- **Headroom first.** The operations benchmark ([pre-registration](../../benchmarks/operations/PREREGISTRATION.md)) first runs only arms without worldparts, on 16 development tasks. If frontier agents already pass them, the operations direction stops there.
- **A stronger baseline.** The decisive comparison is worldparts against the same agent given the same method checklist and a reference script: the cheapest competing product, a skill.
- **Product data, BattLeDIM and a Haiku-only case** are not part of the gate. Haiku returns only if the owner names a real small-model deployment.

The raw research reports (about 330 KB, with all citations) are kept outside the repository with the original research report.
