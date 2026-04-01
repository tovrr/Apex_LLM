# AGENTS.md - Operating Rules for AI Agents in Apex-llm

Purpose: define mandatory behavior for any AI coding agent working in this repository.

## 1) Core Principles

1. Do not invent facts. If uncertain, say it clearly and verify with tools.
2. Prefer evidence over assumptions. Every important claim must be traceable to files, logs, or command output.
3. Minimize side effects. Do only what was asked.
4. Preserve user trust. If a mistake is made, acknowledge it immediately and correct it.

## 2) Before Any Change

1. Confirm the exact user request in one short sentence.
2. Inspect existing files and environment before creating new files/folders.
3. If multiple valid approaches exist, pick the least risky one.
4. Never create new virtual environments if one already exists and is active.

## 3) Environment Policy (Critical)

1. Canonical Python environment for this repo: ./venv
2. Do not create .venv, env, or any additional Python environment folder unless the user explicitly requests it.
3. Before any Python/package action, verify:
   - VIRTUAL_ENV points to ./venv
   - python executable is from ./venv/Scripts/python.exe
4. If environment mismatch is detected, stop and ask for confirmation before proceeding.

## 4) Security and Secrets

1. Never hardcode API keys, tokens, passwords, or secrets in source files.
2. Use environment variables and .env.example templates only.
3. Never print secret values in chat responses or logs.
4. If a secret was committed, report immediately and recommend rotation.

## 5) Editing Rules

1. Do not modify files outside scope.
2. Keep changes small and targeted.
3. Do not touch apex_core.py or apex_logic.py unless explicitly requested.
4. Do not run destructive commands unless explicitly approved.

## 6) Validation Rules

1. After edits, run quick checks relevant to the change (lint/errors/startup checks when possible).
2. If checks cannot run, state that explicitly.
3. Report residual risks and assumptions clearly.

## 7) Communication Rules

1. Give short progress updates during multi-step work.
2. Separate facts from hypotheses.
3. When referencing code, provide exact file locations.
4. Do not claim actions that were not actually executed.

## 8) Incident Prevention (Hallucination and Drift)

1. If a prior answer was wrong, correct the record explicitly.
2. Do not continue with uncertain context; verify first.
3. If a tool output conflicts with prior assumptions, trust the tool output.
4. For repo audits, include only findings backed by direct evidence.

## 9) Definition of Done

A task is done only when:

1. Requested changes are implemented.
2. Constraints are respected.
3. Basic validation is completed or limitations are disclosed.
4. Output summary is precise, with no speculative claims.
