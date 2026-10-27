document.addEventListener("DOMContentLoaded", () => {
    const button = document.getElementById("accessibility-button");
    const panel = document.getElementById("accessibility-panel");
    const close = document.getElementById("close-accessibility");
    const themeSelect = document.getElementById("theme-setting");
    const colorSelect = document.getElementById("color-setting");
    const reset = document.getElementById("reset-accessibility");

    const allowedThemes = ["light", "dark", "system"];
    const allowedColors = [
        "default", "red-green", "blue-yellow", "high-contrast"
    ];

    function getSetting(key, allowed, fallback) {
        try {
            const value = localStorage.getItem(key);
            return allowed.includes(value) ? value : fallback;
        } catch {
            return fallback;
        }
    }

    function saveSetting(key, value) {
        try {
            localStorage.setItem(key, value);
        } catch {
            // Settings still work for this page if storage is blocked.
        }
    }

    function applySettings() {
        const selectedTheme = themeSelect.value;
        const theme = selectedTheme === "system"
            ? (window.matchMedia("(prefers-color-scheme: dark)").matches
                ? "dark" : "light")
            : selectedTheme;

        document.documentElement.dataset.theme = theme;
        document.documentElement.dataset.colorMode = colorSelect.value;

        saveSetting("da-theme", selectedTheme);
        saveSetting("da-color-mode", colorSelect.value);
    }

    themeSelect.value = getSetting(
        "da-theme", allowedThemes, "system"
    );

    colorSelect.value = getSetting(
        "da-color-mode", allowedColors, "default"
    );

    applySettings();

    themeSelect.addEventListener("change", applySettings);
    colorSelect.addEventListener("change", applySettings);

    button.addEventListener("click", () => {
        panel.hidden = false;
        button.setAttribute("aria-expanded", "true");
        themeSelect.focus();
    });

    function closePanel() {
        panel.hidden = true;
        button.setAttribute("aria-expanded", "false");
        button.focus();
    }

    close.addEventListener("click", closePanel);

    document.addEventListener("keydown", event => {
        if (event.key === "Escape" && !panel.hidden) {
            closePanel();
        }
    });

    reset.addEventListener("click", () => {
        themeSelect.value = "system";
        colorSelect.value = "default";
        applySettings();
    });

    window.matchMedia("(prefers-color-scheme: dark)")
        .addEventListener("change", () => {
            if (themeSelect.value === "system") applySettings();
        });
});