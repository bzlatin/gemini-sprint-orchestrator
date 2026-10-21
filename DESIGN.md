# Terminal design

The dashboard is an operating surface for repeated keyboard use in SSH and tmux. Native terminal constraints take precedence over web typography, image generation, or browser workflows. The explicit implementation brief supplies product context; no additional design interview is needed for this task.

Use a dense, aligned ticket table as the primary workspace. Persistent textual lifecycle labels carry meaning independently of color. Details, approval preview and review evidence occupy dedicated tabs. The primary action is always named; explicit confirmation interrupts only consequential approvals.

Palette: background #101820, panel #182630, foreground #edf2f4, muted #b7c8cf, accent #6de0cb, warning #ffd28a, error #ff9e9e. Spacing follows terminal character cells. Borders divide work areas rather than decorating cards. Active focus and selected rows remain obvious. No invented completion percentages.

At 120×40 the ticket table and details share the main tab vertically; at 80×24 tabs and scroll containers keep actions reachable. Keyboard bindings support selection, planning, refresh, navigation and exit. Screen reader users can use the equivalent CLI workflow.
