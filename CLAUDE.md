# CLAUDE.md

### 1. Git Commit Messages

- **NEVER** include AI, Claude, or automation references in commit messages
- **NEVER** add "Co-Authored-By: Claude <noreply@anthropic.com>"
- **NEVER** add "Generated with [Claude Code](https://claude.com/claude-code)"
- Write commits as if a human developer wrote them
- Keep messages professional and concise

**BAD EXAMPLE:**

```
Fix PostgreSQL syntax errors

- Fixed INSERT OR REPLACE issues
- Updated column names

Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
```

**GOOD EXAMPLE:**

```
Fix PostgreSQL syntax errors

- Fixed INSERT OR REPLACE issues
- Updated column names
```

### 2. Git Identity

- This is a **personal project**.
- Always commit as:
  - name: `jeevanba273`
  - email: `jeevanscience273@gmail.com`
- Never use the company email for this repository.
- Already set locally. Verify with `git config user.email` before the first commit of a session.

### 3. Never Push Without Asking

- Commit only when explicitly asked. **Never push to `main` without approval.**
- All work happens on a feature branch. `main` is what Railway deploys to production.
- **Production is currently 15 months behind `main`.** A push to `main` triggers a deploy that
  publishes everything at once. Treat every push as a release.
