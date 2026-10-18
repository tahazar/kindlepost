# Repo rules

## Commits and pull requests
- No AI attribution anywhere: no `Co-Authored-By` trailers, no session links, no "Generated with" footers in commits, PR descriptions, or review comments.
- Commit messages: one imperative subject line, no body unless the change needs explaining.

## Writing style (docs, comments, PRs, commit messages)
- State things directly. No contrast framing such as "it's not X, it's Y".
- No meta commentary: don't describe the writing, the change history, or what the reader is about to see. Code comments explain why the code does what it does, nothing else.
- No filler openers or closers, no hype words, no summaries that repeat what was just said.

## Secrets
- Never commit, print, or log `API_TOKEN` or any other credential. `.env` stays gitignored.
- Scripts tell the user how to read a secret; they don't echo it.
- Don't put real hostnames, tailnet names, IPs, or tokens in docs or examples; use placeholders like `SERVER`.
