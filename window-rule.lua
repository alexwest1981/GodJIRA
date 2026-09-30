-- GodJIRA: the window rule the installer adds to ~/.config/hypr/hyprland.lua.
--
-- The panel is a real tile by default, like any other app window. This rule makes
-- it float and centre itself instead. install.sh appends the line below (or
-- replaces an older GodJIRA rule) and reloads Hyprland; nothing here is read at
-- runtime.
--
-- The marker below is how the installer finds its own line again.
-- godjira-window-rule
o.window({ class = "^org.quickshell$", title = "^Jira$" }, { float = true, center = true })
