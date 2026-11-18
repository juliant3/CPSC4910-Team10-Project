
let voiceEnabled = false;
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
    if (element.matches('[data-no-speech]')) {
    return element.getAttribute("placeholder") || "Password field";
}
    if (isSensitive(element)) return "";
    if (element.querySelector('[data-no-speech]')) {
    return "";
}

    // Only announce labels, never entered values.
    if (element.matches("input, textarea, select")) {
        const labels = element.labels;

        if (labels && labels.length > 0) {
            return labels[0].textContent.trim();
        }

        return element.getAttribute("aria-label") ||
               element.placeholder ||
               "";
    }

    // Do not announce password field containers.
    if (element.querySelector('input[type="password"]')) {
        return "";
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
    stopReading();

    const button = document.getElementById("voiceToggle");

    if (button) {
        button.textContent = voiceEnabled
            ? "🔊 Voice On"
            : "🔇 Voice Off";

        button.setAttribute("aria-pressed", String(voiceEnabled));
    }

    if (voiceEnabled) {
        speak("Voice navigation enabled");
    }
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
