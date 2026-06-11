"""Help text rendering for session browser."""


def render_help_text() -> str:
    """Render comprehensive help text for command output."""
    return """
CHECKPOINT SESSION BROWSER - HELP

NAVIGATION
  ↑/↓ or j/k         Move selection up/down
  ←/→ or h/l         Switch between provider tabs
  [ / ]              Jump to previous/next session (skip turns)
  PageUp/PageDown    Scroll tree or output pane
  Home / End         Jump to first/last row in current view
  Tab                Expand all sessions
  Shift+Tab          Collapse all sessions

ACTIONS
  Enter              Toggle expand/collapse for selected session
  r                  Show resume command for selected checkpoint
  d                  Show diff for selected turn
  /                  Enter command mode (type /show, /diff, /resume, /quit)
  ?  or F1           Toggle help panel
  q  or Esc          Quit browser (or cancel current mode)

OUTPUT PANE
  Ctrl+↑/↓           Resize output pane (when visible)
  PageUp/PageDown    Scroll output when it has overflow; otherwise scroll tree
  Ctrl+F/Ctrl+B      Scroll output pane directly
  Home/End           Jump to output top/bottom when output has overflow

RESUMABLE CHECKPOINTS
  A checkpoint is resumable if:
    - It belongs to a parent session (not a subagent)
    - The session was fully captured (no [no capture] marker)
    - It has complete trajectory data

  Non-resumable sessions will show:
    - SUBAGENT badge: Cannot resume subagent sessions
    - NO CAPTURE badge: Session wasn't fully captured

SYMBOLS
  ▶/▼                Session collapsed/expanded indicator
  ├── └──            Tree structure connections
  │                  Tree continuation line
  ↪                  Fork relationship
  ⚡                 Subagent relationship
  ⋯                  More rows above/below (scroll to see)
  …                  Text truncated

COMMANDS (via / key)
  /show              Show full checkpoint manifest as JSON
  /diff              Show changes that would be applied by resume
  /resume            Show the checkpoint resume command
  /quit              Exit browser
  /help              Display this help text

NOTE: Use 'checkpoint clean --empty' CLI command to remove empty sessions

Press ? or F1 to hide this help panel.
"""
