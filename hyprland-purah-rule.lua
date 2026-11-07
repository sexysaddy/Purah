-- Hyprland 0.55+ Lua configuration.
-- Blur translucent parts of Purah's layer while ignoring near-transparent pixels.
hl.layer_rule({
    name = "purah-overlay-glass-blur",
    match = { namespace = "^purah-overlay$" },
    blur = true,
    ignore_alpha = 0.08,
    blur_popups = false,
    no_anim = true,
})
