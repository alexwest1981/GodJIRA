-- GodJIRA (custom.jira): float the Jira window over the tiling and center it.
--
-- The panel is a normal Quickshell window, so Hyprland tiles it like any
-- other app unless a window rule floats it. Add these lines once to the end
-- of ~/.config/hypr/hyprland.lua, then save (Hyprland reloads on save) or run:
--
--   hyprctl reload
--
-- The window then floats and centers, and honours its own size limits
-- (min 720x500, fitted to the screen). Without this rule the window opens
-- tiled instead of floating.

o.window({ class = "^org.quickshell$", title = "^Jira$" }, { float = true, center = true })
