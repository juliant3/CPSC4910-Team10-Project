
let voiceEnabled = localStorage.getItem("da-voice-navigation") === "true";
let hoverTimer = null;
let lastElement = null;

function stopReading() {
    clearTimeout(hoverTimer);
    window.speechSynthesis.cancel();
}

function speak(text) {
    if (!voiceEnabled || !text || !text.trim()) return;

    stopReading();

    const message = new SpeechSynthesisUtterance(text.trim());
    message.lang = "en-US";
    message.rate = 0.9;

    window.speechSynthesis.speak(message);
}

function isSensitive(element) {
    return !!element.closest(
        'input[type="password"], ' +
        'input[type="hidden"], ' +
        '[data-no-speech]'
    );
}

function getReadableText(element) {

    // Password fields: announce instructions, never the value.
    if (element.matches('input[type="password"], input[data-no-speech]')) {
        return element.getAttribute("placeholder") || "Enter your password";
    }

    // Skip other sensitive elements.
    if (isSensitive(element)) return "";

    // Never read entered form values.
    if (element.matches("input, textarea, select")) {
        return element.getAttribute("aria-label") ||
               element.labels?.[0]?.textContent.trim() ||
               element.placeholder ||
               "";
    }

    return element.getAttribute("aria-label") ||
           element.textContent.trim() ||
           "";
}

function readElement(element) {
    if (!voiceEnabled || !element) return;

    if (element.closest('[hidden], [aria-hidden="true"]')) {
        return;
    }

    const text = getReadableText(element);

    if (text) speak(text);
}

document.addEventListener("mouseover", function(event) {
    if (!voiceEnabled) return;

    const element = event.target.closest(
        "button, a, input, select, textarea"
    );

    if (!element || element === lastElement) return;

    clearTimeout(hoverTimer);
    lastElement = element;

    hoverTimer = setTimeout(function() {
        readElement(element);
    }, 500);
});

document.addEventListener("mouseout", function(event) {
    const element = event.target.closest(
        "button, a, input, select, textarea"
    );

    if (element && !element.contains(event.relatedTarget)) {
        clearTimeout(hoverTimer);
        lastElement = null;
    }
});

document.addEventListener("focusin", function(event) {
    if (!voiceEnabled) return;

    const element = event.target.closest(
        "button, a, input, select, textarea"
    );

    if (element) readElement(element);
});


function toggleVoice() {
    voiceEnabled = !voiceEnabled;

    // Save the user's preference.
    localStorage.setItem(
        "da-voice-navigation",
        String(voiceEnabled)
    );

    stopReading();

    updateVoiceButton();

    if (voiceEnabled) {
        speak("Voice navigation enabled");
    }
}


function updateVoiceButton() {
    const button = document.getElementById("voiceToggle");

    if (!button) return;

    button.textContent = voiceEnabled
        ? "Voice On"
        : "Voice Off";

    button.setAttribute(
        "aria-pressed",
        String(voiceEnabled)
    );
}


function readPage() {
    const main = document.getElementById("main-content");
    if (!main) return;

    stopReading();

    const copy = main.cloneNode(true);

    copy.querySelectorAll(
        'input, textarea, select, script, style, ' +
        'button, [hidden], [aria-hidden="true"], ' +
        '[data-no-speech], form'
    ).forEach(element => element.remove());

    const text = copy.textContent.trim();
    if (!text) return;

    const message = new SpeechSynthesisUtterance(text);
    message.lang = "en-US";
    message.rate = 0.9;

    window.speechSynthesis.speak(message);
}

document.addEventListener("DOMContentLoaded", function() {
    updateVoiceButton();
});