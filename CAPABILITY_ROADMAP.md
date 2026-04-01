# Apex Capability Roadmap

## 1. Purpose

This document defines what Apex should learn next, in what order, and how to evaluate whether each capability upgrade is worth shipping.

The goal is to avoid training blindly.

Apex should improve through a sequence of:

1. better measurement,
2. better retrieval and context,
3. better experts,
4. better routing,
5. better training and adaptation.

## 2. Guiding Principle

Do not scale training first.

Improve Apex in this order:

1. evaluation,
2. product instrumentation,
3. retrieval and memory,
4. expert capabilities,
5. fine-tuning,
6. larger model and systems research.

## 3. Capability Priority Stack

### Priority 0: Evaluation and Feedback Loop

Before new training work, Apex needs a reliable loop for measuring improvement.

Build first:

1. golden prompt sets from real user tasks,
2. failure logging and clustering,
3. success metrics by task type,
4. regression suite before each release,
5. latency and cost tracking by route and model.

Definition of success:

1. every important model change can be compared against a baseline,
2. performance regressions are caught before shipping,
3. product decisions are guided by real prompt data.

## 4. Retrieval Roadmap

### Why Retrieval Comes Early

Apex does not need to memorize everything.

It needs to access the right context at the right time.

### Retrieval Stage 1: Local Project Context

Target use cases:

1. code understanding,
2. repo Q&A,
3. debugging existing systems,
4. internal docs lookup.

Required features:

1. document and file chunking,
2. embedding + vector search,
3. source citations in answers,
4. scoped retrieval by folder/project.

Initial data sources:

1. source files,
2. markdown docs,
3. API specs,
4. internal product docs.

### Retrieval Stage 2: User and Workspace Context

Target use cases:

1. personalized responses,
2. project continuity,
3. multi-session workflow memory.

Required features:

1. session memory,
2. user preferences,
3. project metadata,
4. recent run history context.

### Retrieval Stage 3: Team and Knowledge Base Context

Target use cases:

1. support workflows,
2. internal SOPs,
3. business operations guidance.

Required features:

1. shared workspace collections,
2. team policy access,
3. permission-aware retrieval,
4. freshness and invalidation policy.

Evaluation gates for retrieval:

1. answer grounding quality,
2. retrieval precision at top-k,
3. hallucination reduction,
4. user trust improvement.

## 5. Expert Roadmap

Apex should not train too many experts at once.

Start with three experts that map to real monetizable workflows.

### Expert 1: Code and Debugging

Focus:

1. bug diagnosis,
2. code review,
3. patch generation,
4. API integration help.

Candidate datasets:

1. high-quality code review examples,
2. bug-fix commits and explanations,
3. repo-specific curated prompt-response pairs,
4. internal successful developer sessions.

Evaluation tasks:

1. patch correctness,
2. root-cause identification,
3. regression avoidance,
4. compile/test pass rate.

### Expert 2: Product and Business Writing

Focus:

1. launch copy,
2. product announcements,
3. sales/support messaging,
4. concise business summaries.

Candidate datasets:

1. product marketing examples,
2. release notes,
3. support macro libraries,
4. curated writing style corpora.

Evaluation tasks:

1. clarity,
2. tone match,
3. brevity quality,
4. usefulness to product teams.

### Expert 3: Operations and Support

Focus:

1. troubleshooting guidance,
2. SOP-based answers,
3. internal knowledge workflows,
4. customer-facing support assistance.

Candidate datasets:

1. FAQ sets,
2. incident summaries,
3. support tickets with verified resolutions,
4. process documentation.

Evaluation tasks:

1. instruction correctness,
2. escalation accuracy,
3. policy consistency,
4. resolution usefulness.

## 6. Dataset Roadmap

### Data Sources to Build First

1. real Apex and Quill user prompts,
2. accepted outputs from live sessions,
3. failed outputs with corrected answers,
4. domain documents and repos,
5. curated benchmark subsets relevant to chosen wedge.

### Data Governance Rules

1. collect with clear consent where needed,
2. redact secrets and personal data,
3. separate raw logs from training-ready datasets,
4. keep a labeled "gold" evaluation set untouched by training.

### What Not to Do

1. do not rely only on generic internet data,
2. do not train on noisy logs without curation,
3. do not mix evaluation data into training data.

## 7. Evals Roadmap

Apex needs four eval lanes.

### Lane 1: Product Evals

1. real task completion,
2. user-rated usefulness,
3. edit acceptance rate,
4. follow-up reduction.

### Lane 2: Technical Evals

1. coding accuracy,
2. structured output validity,
3. retrieval grounding,
4. latency and cost.

### Lane 3: Safety and Trust Evals

1. hallucination frequency,
2. refusal correctness,
3. policy adherence,
4. secret leakage prevention.

### Lane 4: Route Quality Evals

1. did the router choose the right model/expert,
2. was the expensive path justified,
3. quality-per-cost of each routing decision.

## 8. Upgrade Sequence (2026 to 2027)

### Step 1: Instrumentation

Deliver:

1. request logging,
2. run tagging by task type,
3. user feedback capture,
4. golden evaluation sets.

### Step 2: Retrieval V1

Deliver:

1. local repo and docs retrieval,
2. context-aware answer generation,
3. citation support.

### Step 3: Expert V1

Deliver:

1. code expert,
2. writing expert,
3. ops/support expert.

### Step 4: Router V1

Deliver:

1. route simple tasks to Flash,
2. route deep tasks to Reasoning,
3. route domain tasks to experts.

### Step 5: Fine-Tuning V2

Deliver:

1. domain-tuned adapters from real usage data,
2. failure-driven improvement loops,
3. measurable uplift on golden tasks.

### Step 6: Gateway-Aware Intelligence

Deliver:

1. plan-aware routing,
2. quota-aware routing,
3. premium reasoning paths for paid tiers.

### Step 7: Mobile and Team Context

Deliver:

1. shared context across web and mobile,
2. team memory and workspace knowledge,
3. multi-device continuity.

## 9. Recommended First Datasets

For the next 60 to 90 days, prioritize:

1. real Quill/Apex prompts and accepted outputs,
2. curated code-debug examples,
3. internal docs and product knowledge chunks,
4. support and operations examples,
5. bilingual or multilingual task sets if multilingual quality is a differentiator.

## 10. Shipping Criteria for New Capability Work

A new capability is worth shipping only if it improves at least one of:

1. task success rate,
2. user retention,
3. response trustworthiness,
4. cost per successful task,
5. latency-adjusted usefulness.

## 11. Immediate Recommendations

### Right Now

1. build eval instrumentation first,
2. add retrieval before major new training,
3. train only the first three experts,
4. use real user data to drive future fine-tuning.

### Not Yet

1. giant base-model ambitions,
2. too many experts,
3. broad consumer use cases without a wedge,
4. expensive retraining without measurement.

## 12. Final Rule

Apex should only become smarter in ways that improve the product users actually pay for.
