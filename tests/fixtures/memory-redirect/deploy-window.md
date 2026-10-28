---
name: deploy-window
description: Directive — run disruptive deploys between 02:00 and 09:00 ET; never ask for a window
metadata:
  type: feedback
---
Operator, 2026-10-27: "dont ask for a deploy window, use the morning slot".

**Why:** asking for a window cost a round trip every time.

**How to apply:** schedule the restart inside the slot and report it.
