# ASMP Git Ritual — Claude Code Instructions

You are assisting Rick with active development on the **ASMP (Aroxx Systems Management Platform)**.
Repo: `github.com/rbrady47/smp.git`

## Your Git Responsibilities

Before starting any work, and at natural breakpoints during a session, you must enforce the following ritual. Do not skip steps, and do not proceed with code changes if the repository is in a bad state.

---

## 1. Pre-Work Checklist (Run at Session Start)

Before writing any code, run and report on:

```bash
git status
git branch
git log --oneline -10
```

**Check for:**
- Are we on a feature branch? (Never work directly on `main`)
- Is the working tree clean? (No uncommitted changes)
- Is there stashed work that should be addressed first? (`git stash list`)

**If on `main`:** Stop. Ask Rick what feature he's working on and create a branch:
```bash
git checkout -b feature/<short-descriptive-name>
```

**If working tree is dirty:** Stop. Do not proceed until Rick decides to commit, stash, or discard the existing changes.

---

## 2. Branch Naming Convention

| Type | Pattern | Example |
|---|---|---|
| New feature | `feature/<name>` | `feature/topology-edge-drawing` |
| Bug fix | `bugfix/<name>` | `bugfix/postgres-auth-fix` |
| Refactor | `refactor/<name>` | `refactor/rename-smp-to-asmp` |
| Chore/config | `chore/<name>` | `chore/update-gitignore` |

---

## 3. Commit Protocol

After completing any logical unit of work, prompt Rick to commit before continuing. Do not let work accumulate across multiple features without committing.

**Review before staging:**
```bash
git diff
```

**Stage interactively — never use `git add .` blindly:**
```bash
git add -p
```

**Commit with a conventional message:**
```bash
git commit -m "<type>: <short description>"
```

**Commit message types:**
- `feat:` — new feature or capability
- `fix:` — bug fix
- `refactor:` — code restructure, no behavior change
- `chore:` — config, dependencies, tooling
- `docs:` — documentation only
- `wip:` — work in progress (use sparingly, always follow up)

**Examples:**
```
feat: add edge creation endpoint to topology API
fix: correct pg_hba.conf Unix socket auth
chore: add .env and *.pem to .gitignore
refactor: rename SMP references to ASMP
```

---

## 4. Things to Never Do

- ❌ Never commit directly to `main`
- ❌ Never use `git add .` without reviewing changes first
- ❌ Never commit `.env`, `*.pem`, `*.key`, `*.crt`, or any credentials
- ❌ Never let a session end with uncommitted work unless it's explicitly stashed with a label
- ❌ Never push to `origin main` without Rick's explicit sign-off

---

## 5. End-of-Session Checklist

Before ending a Claude Code session, run:

```bash
git status
git log --oneline -5
```

Then confirm:
- [ ] All intended work is committed
- [ ] Any unfinished work is stashed with a message: `git stash push -m "wip: <description>"`
- [ ] Branch is pushed to remote if work is ready: `git push origin <branch-name>`

Report the final state to Rick clearly.

---

## 6. Merging to Main

Only suggest merging to `main` when:
- The feature works end-to-end
- Rick explicitly says it's ready
- The working tree is clean

```bash
git checkout main
git pull
git merge feature/<name>
git push origin main
git branch -d feature/<name>
git push origin --delete feature/<name>
```

---

## 7. Red Flags — Stop and Alert Rick

Immediately stop and flag if you notice:
- A `.env` file or any credentials in the working tree that are tracked by git
- Uncommitted changes from a previous session that don't match the current task
- The repo is on `main` with a dirty working tree
- Merge conflicts that need human judgment
- More than ~10 unreviewed changed files in a single commit candidate
