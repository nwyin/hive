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

### Prompt 7

❯ what is local hive. what is .hive/local-hive.db -- like these shouldn't be created? we should always use the systems' ~/hive.db?

### Prompt 8

may be a manual db override becuase it exists in ~/projects/pycg-rs and ~/projects/pycfg-rs

### Prompt 9

please do

### Prompt 10

wait so does the queen-state and queen-instruction get cleaned up when we end the hive queen process?

### Prompt 11

interesting...so this gets noisy, yeah? within the project is a git tree, and as the queen is update .claude/CLAUDE.md and the queen state, the merges need to keep in mind these dirty files OR make a chocie of commiting them as well.

I suppose you could specifically ignore .hive/queen-state.md but like hive init doesn't do that for you

what are better solutions here? we have these state files that i've been accidentally committing/uncommitting -- there's certianly a better way to do this

### Prompt 12

[Request interrupted by user]

### Prompt 13

keep going

### Prompt 14

what about the .claude/CLAUDE.md file?

### Prompt 15

yeah these seem like reasonable ish solutions. let's go with this for now, and maybe make a note in docs/*.md -- one of those files -- on revisiting this design choice

### Prompt 16

what happened to the `hive daemon` command? we should also be able to start up the hive daemon from anywhere, not just within a hive project, now that we have theheadless mode and coordination multiple projects

### Prompt 17

[Request interrupted by user]

### Prompt 18

⏺ It's hive start, not hive daemon. The queen's headless mode is trying to start a daemon via a daemon subcommand that doesn't exist — it should be using start. This is a bug in hive
  itself.

  Looks like queen headless mode has a broken daemon launch path. Two options:

  1. Fix the bug in hive (the headless code is calling daemon start instead of start)
  2. Start the daemon manually first, then run queen headless

### Prompt 19

All three show the same error:

  Starting daemon... Error: Failed to start daemon. Check `hive daemon logs`.
  failed

this was the logs

### Prompt 20

reinstall the tool on the system

### Prompt 21

~/projects/hive (main)
$ uv tool install --force --reinstall -e .
Resolved 19 packages in 262ms
      Built hive @ file:///Users/tau/projects/hive
Prepared 19 packages in 315ms
Uninstalled 19 packages in 40ms
Installed 19 packages in 10ms
 ~ aiohappyeyeballs==2.6.1
 ~ aiohttp==3.13.3
 ~ aiosignal==1.4.0
 ~ annotated-doc==0.0.4
 ~ attrs==25.4.0
 ~ click==8.3.1
 ~ frozenlist==1.8.0
 ~ hive==0.1.0 (from file:///Users/tau/projects/hive)
 ~ idna==3.11
 ~ markdown-it-py==4.0.0
 ~ mdurl==0.1.2
 ~ mu...

### Prompt 22

[Request interrupted by user]

### Prompt 23

keep going

### Prompt 24

[Request interrupted by user]

### Prompt 25

revert all the changes, this is starting to get silly. we'll rethink our approach then implement

