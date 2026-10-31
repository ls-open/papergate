---
bibliography: references.bib
link-citations: true
reference-section-title: References
title: "Review Latency in Collaborative Document Editing: A Simulation Study"
abstract: |
  **Purpose**: This study examines how review latency affects the throughput of distributed teams that edit documents in parallel. The goal is to separate the delay introduced by waiting for review from the delay introduced by the editing process itself.

  **Design/methodology/approach**: We build a discrete-event simulation of a shared document with a versioned history. Each edit creates a revision, and each revision must be approved before it can be merged. We vary reviewer capacity, batch size, and the probability of conflicting edits across 1,200 simulated runs.

  **Findings**: Review latency dominates total delivery time once reviewer capacity falls below one approval per editing interval. Larger review batches reduce coordination overhead but increase the time each revision waits before it is examined. Under high conflict rates, small batches are consistently faster.

  **Originality/value**: The model isolates review latency as a design variable rather than a fixed cost. It gives teams a way to compare batching policies before changing their workflow, and it shows why "review faster" and "review less" are not the same intervention.
keywords: Collaborative Editing; Review Latency; Discrete-event Simulation; Version Control; Team Throughput
---

# 1. Introduction

Distributed teams rarely write a document in a single pass. They edit in parallel, create revisions, and wait for someone else to approve those revisions before the document moves forward. The waiting time is usually treated as an unavoidable cost of coordination [@ref01; @ref02; @ref03]. This paper treats it as a design variable.

The distinction matters because two interventions that look similar have different effects. Shortening the time a reviewer spends on each revision changes the service rate. Reducing the number of revisions that need review changes the arrival rate. A team that only measures total delivery time cannot tell which of the two is limiting throughput [@ref04; @ref05; @ref06].

We therefore build a minimal simulation in which editing and review are separate processes. The model is deliberately small: it does not attempt to represent document content, reviewer expertise, or organizational authority. It represents only the timing of edits, the timing of approvals, and the conflicts that occur when two edits touch the same region.

The contribution is threefold. First, we isolate the point at which review capacity becomes the binding constraint. Second, we show that the optimal review batch size depends on the conflict rate rather than on the average review time. Third, we provide a reproducible model that a team can adapt to its own arrival and service rates.

# 2. Related Work

Research on collaborative editing has focused mainly on consistency. Operational transformation and conflict-free replicated data types guarantee that concurrent edits converge to a single document state [@ref07; @ref08; @ref09; @ref10]. These mechanisms answer the question of what the document should contain after a conflict. They do not answer the question of when a revision becomes available for the next step.

Studies of code review describe the review queue as a source of delay [@ref11; @ref12; @ref13]. Their evidence comes from repositories where review is already an established practice. Document editing in smaller teams is less instrumented, and the same queue behavior is often invisible because edits and approvals share a single timeline.

Queueing models provide a natural language for the problem. Little's law relates the number of revisions in the system to arrival rate and time in system [@ref14]. Simulation is appropriate here because the service process is not memoryless: reviewers batch revisions, and batching changes the effective service time [@ref15; @ref16].

# 3. Method

We model a document as a sequence of regions. Each edit targets one region. If two edits target the same region before either is approved, the later edit is marked as conflicting and must be rebased onto the approved revision. This simplification captures the part of editing that creates serialization without modeling text itself [@ref17].

Edits arrive according to a Poisson process with rate lambda. A reviewer processes a batch of up to k revisions, and the time to process a batch is proportional to its size plus a fixed setup cost [@ref18; @ref19]. Approved revisions leave the queue; conflicting revisions re-enter after a rebase delay. The simulation runs for 10,000 time units and discards the first 1,000 units as warm-up.

We vary three parameters: reviewer capacity relative to the editing rate, batch size k, and conflict probability p [@ref20; @ref21]. Each configuration is repeated 40 times with independent random seeds. The primary outcome is mean delivery time per revision, defined as the interval between the edit and its approval. The secondary outcome is the fraction of revisions that conflict at least once.

The model and its parameters are intentionally transparent. A team can replace the arrival rate and service time with its own measurements and obtain the same comparison between batching policies.

# 4. Results

Review capacity is the dominant factor [@ref22]. When capacity exceeds the editing rate, delivery time stays close to the service time regardless of batch size. When capacity falls below the editing rate, delivery time grows without bound, and the queue becomes the system.

Batch size has an asymmetric effect [@ref23]. Increasing k reduces setup overhead and raises effective capacity, but it also increases the time a revision spends waiting for the batch to close. At low conflict rates, larger batches are faster because the capacity gain outweighs the waiting cost. At high conflict rates, larger batches are slower because they group revisions that are more likely to conflict, and the rebase work grows with the batch.

Conflict probability changes the ranking of policies rather than the average [@ref24]. With p equal to 0.1, a batch of eight revisions delivers about the same mean latency as a batch of two. With p equal to 0.4, the small batch is consistently faster, and the gap widens as capacity decreases. The reason is that a conflict inside a batch forces the whole batch to wait for a rebase, so batching converts independent delays into a shared delay.

These results suggest that the common advice to "review in larger batches" is conditional. It holds when conflicts are rare and reviewer capacity is the bottleneck. It fails when conflicts are frequent, which is precisely the case in documents with tightly coupled sections.

# 5. Discussion

The model separates two levers that are often conflated in practice [@ref25; @ref26; @ref27]. Adding reviewer capacity raises the service rate. Reducing batch size lowers the shared-delay penalty. A team that measures only end-to-end delivery time cannot tell which lever is binding, and may invest in the wrong one.

The result also explains why some teams report that faster reviews did not improve delivery. If the queue is stable and conflicts are frequent, reducing review time shortens service but leaves the rebase delay untouched. The binding constraint is the conflict rate, not the reviewer's speed [@ref28; @ref29; @ref30].

The model has clear limits. It ignores reviewer expertise, document structure, and the possibility that a revision is abandoned rather than merged. It also assumes that the conflict probability is constant across regions, which is unlikely in real documents. These assumptions make the results directional rather than predictive.

# 6. Conclusion

Review latency is not a fixed cost of coordination. It is the outcome of three interacting choices: reviewer capacity, batch size, and the conflict rate of the document. Our simulation shows that the first two are substitutes only when the third is low. When conflicts are frequent, smaller batches and higher capacity both help, but for different reasons.

The practical recommendation is to measure the conflict rate before changing review policy. A team that batches aggressively to reduce overhead may be trading a visible delay for a hidden one. The model is small enough to reproduce and specific enough to argue with, which is the point of a simulation study.
