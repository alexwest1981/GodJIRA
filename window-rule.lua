-- GodJIRA (custom.jira): OPTIONAL rule that floats the Jira window, kept as a recipe.
--
-- NOT in force. The panel is a real tile now: ~/.config/hypr/hyprland.lua has
--     o.window({ class = "^org.quickshell$", title = "^Jira$" }, { tile = true })
-- which is also what Hyprland does by default (a Quickshell window tiles like any
-- other app when no rule floats it). Copy the line below to the end of that file,
-- save (Hyprland reloads on save) or run `hyprctl reload`, to get the old behaviour.
--
-- The floated window centres itself and honours its own size limits (min 720x500,
-- fitted to the screen).

o.window({ class = "^org.quickshell$", title = "^Jira$" }, { float = true, center = true })
