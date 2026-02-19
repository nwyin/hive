# Session Context

## User Prompts

### Prompt 1

read @README.md and @docs/TECHNICAL_DESIGN_DOC.md

https://code.claude.com/docs/en/agent-teams

so i'm essentially re-implemeting anthropic's built-in, experimental feature.

i want to "reverse engineer" anthropic's feature. like how does it exactly work

what is the shared task list (looks like a plaintext file at a shared location)

how do agents inter-communicate

how effective is it? how can we benchmark it (artificially or whatever)

and how can we compare it to hive's approach?


i want to...

### Prompt 2

[Request interrupted by user for tool use]

### Prompt 3

keep going, do take advantage of subagents

### Prompt 4

[Request interrupted by user for tool use]

### Prompt 5

ok you have to have enough info by now

### Prompt 6

ok can you please write this out to *.md

### Prompt 7

[Request interrupted by user]

### Prompt 8

ca you also include citations -- like where did you find this json claim for teammate discovery in claude, etc, etc

### Prompt 9

# De-Slop Command

Remove AI-generated artifacts before PR submission.

## Workflow

### 1. Context & Comparison

**Ask:** Compare against base branch or PR?
```bash
# If base branch
git diff --name-status $(git remote show origin | grep "HEAD branch" | cut -d ":" -f 2 | xargs)...HEAD

# If PR number provided
gh pr view {PR_NUMBER} --json baseRefName -q .baseRefName
git diff {BASE}...HEAD
```

### 2. Scan for Slop (Always Dry Run)

#### A. Unnecessary Markdown Files
Flag: NOTES.md, PLAN.md, ARCH...

