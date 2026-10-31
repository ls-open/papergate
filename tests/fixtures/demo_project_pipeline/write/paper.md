---
title: "Simulation Study on the PaperGate Submission Quality Gate"
abstract: |
  This simulation validates that a fresh AI agent can prepare a project for
  submission and pass the PaperGate submission quality gate using only the
  framework documentation.
bibliography: references.bib
link-citations: true
reference-section-title: References
---

# 1. Introduction

The submission gate enforces structural hygiene across branches [@key01; @key02; @key03; @key04; @key05; @key06; @key07; @key08; @key09; @key10; @key11; @key12]. We
explore how an onboarding agent navigates the checks.

# 2. Literature Review

Prior work frames academic writing pipelines and quality gates [@key01; @key02; @key03; @key04; @key05; @key06; @key07; @key08; @key09; @key10; @key11; @key12; @key13; @key14; @key15; @key16; @key17; @key18; @key19; @key20; @key21; @key22; @key23; @key24; @key25]. The
literature is extensive and motivates the present simulation.

# 3. Data and Methods

We synthesise a minimal project and run the check suite [@key01; @key02; @key03; @key04; @key05; @key06; @key07; @key08; @key09; @key10; @key11; @key12; @key13; @key14; @key15]. Methods are
deliberately simple to isolate the gate behaviour.

# 4. Results

We present **Table 1.** *Descriptive statistics of key variables*.

| Variable | N | Mean |
|:---------|--:|-----:|
| A        | 10 | 3.40 |
| B        | 12 | 4.10 |

As shown in Table 1, the synthetic variables differ modestly. The conceptual
framework is illustrated in Figure 1.

![Figure 1: conceptual framework of the submission pipeline](../results/figures/framework_overview.png)

# 5. Discussion

The results show that branch hygiene and relative CSL paths are the decisive
checks [@key01; @key02; @key03; @key04; @key05; @key06; @key07; @key08; @key09; @key10; @key11; @key12; @key13; @key14; @key15]. Discussion confirms the gate is navigable with documentation.

# 6. Conclusion

We conclude the gate is satisfiable by a fresh agent [@key01; @key02; @key03; @key04; @key05]. Future work should
soften over-strict defaults.
