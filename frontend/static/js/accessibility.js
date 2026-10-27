
document.addEventListener("DOMContentLoaded", () => {

    // ==========================================
    // ACCESSIBILITY SETTINGS ELEMENTS
    // ==========================================

    const button = document.getElementById("accessibility-button");
    const panel = document.getElementById("accessibility-panel");
    const close = document.getElementById("close-accessibility");
    const themeSelect = document.getElementById("theme-setting");
    const colorSelect = document.getElementById("color-setting");
    const reset = document.getElementById("reset-accessibility");

    // Stop if accessibility elements are missing
    if (!button || !panel || !close ||
        !themeSelect || !colorSelect || !reset) {
        return;
    }

    // ==========================================
    // AVAILABLE SETTINGS
    // ==========================================

    const allowedThemes = [
        "light",
        "dark",
        "warm",
        "system"
    ];

    const allowedColors = [
        "default",
        "red-green",
        "blue-yellow",
        "high-contrast"
    ];

    const systemTheme = window.matchMedia(
        "(prefers-color-scheme: dark)"
    );

    // ==========================================
    // LOAD SAVED SETTINGS
    // ==========================================

    function getSetting(key, allowed, fallback) {
        try {
            const value = localStorage.getItem(key);
            return allowed.includes(value) ? value : fallback;
        } catch {
            return fallback;
        }
    }

    // ==========================================
    // SAVE SETTINGS
    // ==========================================

    function saveSetting(key, value) {
        try {
            localStorage.setItem(key, value);
        } catch {
            // Settings still work if storage is unavailable.
        }
    }

    // ==========================================
    // APPLY APPEARANCE AND COLOR SETTINGS
    // ==========================================

    function applySettings() {

        const selectedTheme = allowedThemes.includes(themeSelect.value)
            ? themeSelect.value
            : "system";

        const selectedColor = allowedColors.includes(colorSelect.value)
            ? colorSelect.value
            : "default";

        // System Default follows the user's device preference.
        // Warm Night Mode is a separate appearance option.
        let activeTheme = selectedTheme;

        if (selectedTheme === "system") {
            activeTheme = systemTheme.matches ? "dark" : "light";
        }

        // Update the HTML attributes used by CSS.
        document.documentElement.dataset.theme = activeTheme;
        document.documentElement.dataset.colorMode = selectedColor;

        // Save the selected options, not just the resolved theme.
        saveSetting("da-theme", selectedTheme);
        saveSetting("da-color-mode", selectedColor);
    }

    // ==========================================
    // RESTORE SAVED PREFERENCES
    // ==========================================

    themeSelect.value = getSetting(
        "da-theme",
        allowedThemes,
        "system"
    );

    colorSelect.value = getSetting(
        "da-color-mode",
        allowedColors,
        "default"
    );

    applySettings();

    // ==========================================
    // CHANGE SETTINGS INSTANTLY
    // ==========================================

    themeSelect.addEventListener("change", applySettings);
    colorSelect.addEventListener("change", applySettings);

    // ==========================================
    // OPEN ACCESSIBILITY PANEL
    // ==========================================

    button.addEventListener("click", () => {

        // Toggle the panel when the settings button is clicked.
        if (!panel.hidden) {
            closePanel();
            return;
        }

        panel.hidden = false;
        button.setAttribute("aria-expanded", "true");

        // Move keyboard focus into the settings panel.
        themeSelect.focus();
    });

    // ==========================================
    // CLOSE ACCESSIBILITY PANEL
    // ==========================================

    function closePanel() {
        panel.hidden = true;
        button.setAttribute("aria-expanded", "false");

        // Return keyboard focus to the settings button.
        button.focus();
    }

    close.addEventListener("click", closePanel);

    // Close the panel using Escape.
    document.addEventListener("keydown", event => {
        if (event.key === "Escape" && !panel.hidden) {
            closePanel();
        }
    });

    // ==========================================
    // RESET TO DEFAULT SETTINGS
    // ==========================================

    reset.addEventListener("click", () => {

        themeSelect.value = "system";
        colorSelect.value = "default";

        applySettings();

        // Keep focus inside the settings panel.
        themeSelect.focus();
    });

    // ==========================================
    // AUTOMATIC SYSTEM THEME UPDATES
    // ==========================================

    function handleSystemThemeChange() {
        // Only update automatically when System Default is selected.
        // Do not override Light, Dark, or Warm Night Mode.
        if (themeSelect.value === "system") {
            applySettings();
        }
    }

    if (typeof systemTheme.addEventListener === "function") {
        systemTheme.addEventListener(
            "change",
            handleSystemThemeChange
        );
    } else if (typeof systemTheme.addListener === "function") {
        // Compatibility with older browsers.
        systemTheme.addListener(handleSystemThemeChange);
    }

});
