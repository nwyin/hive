# Session Context

## User Prompts

### Prompt 1

Implement the following plan:

# Plan: Headless Queen Mode

## Context

We want autonomous dispatch of tasks without a new CLI command. The queen already
has project context, filesystem exploration, and knows `hive create`. A headless
mode lets external systems (MC cron, scripts, or the user) pass a task prompt and
have the queen create issues directly — reusing the existing infrastructure.

This replaces the previously discussed `hive dispatch` command.

## Changes

### 1. CLI flags (`src/hi...

### Prompt 2

update docs about htis feature as well

### Prompt 3

alright anything else we might be forgetting? we got tests? doc updates? the feature itself? anything else

### Prompt 4

okay this seems reasonable, commit into main

### Prompt 5

wait so for the headless command, is it project specific. or how can we make it directed towards a project?

### Prompt 6

documnt the --project flag explicitly in the readme when using headless create mode. this will be useful for the PM agent to know

