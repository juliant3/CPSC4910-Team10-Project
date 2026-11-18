
let voiceEnabled = false;
let hoverTimer;
let lastElement = null;

function speak(text) {
    if (!voiceEnabled || !text || !text.trim()) return;

    window.speechSynthesis.cancel();

    const message = new SpeechSynthesisUtterance(text.trim());
    message.rate = 0.9;
    message.lang = "en-US";

    window.speechSynthesis.speak(message);
}

function getReadableText(element) {

    // Never read passwords or other sensitive fields.
    if (element.matches(
        'input[type="password"], input[type="hidden"]'
    )) {
        return "";
    }

    // Never read values entered into form fields.
    if (element.matches("input, textarea, select")) {
        const label = element.labels?.[0]?.innerText;
        const ariaLabel = element.getAttribute("aria-label");

        return label || ariaLabel || element.placeholder || "";
    }

    return element.getAttribute("aria-label") ||
           element.innerText ||
           element.textContent ||
           "";
}

function readElement(element) {

    if (!voiceEnabled || !element) return;

    if (element.closest('[aria-hidden="true"], [hidden]')) return;

    // Skip password fields completely.
    if (element.closest('input[type="password"]')) return;

    const text = getReadableText(element);

    if (text) {
        speak(text);
    }
}

// Read interactive elements on hover.
document.addEventListener("mouseover", function(event) {

    if (!voiceEnabled) return;

    const element = event.target.closest(
        "button, a, input, select, textarea, label"
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
        "button, a, input, select, textarea, label"
    );

    if (element && !element.contains(event.relatedTarget)) {
        clearTimeout(hoverTimer);
        lastElement = null;
    }
});

// Support keyboard navigation.
document.addEventListener("focusin", function(event) {

    if (!voiceEnabled) return;

    clearTimeout(hoverTimer);

    const element = event.target.closest(
        "button, a, input, select, textarea"
    );

    if (element) {
        readElement(element);
    }
});

// Turn voice navigation on or off.
function toggleVoice() {

    voiceEnabled = !voiceEnabled;

    clearTimeout(hoverTimer);
    window.speechSynthesis.cancel();

    const button = document.getElementById("voiceToggle");

    button.textContent = voiceEnabled
        ? " Voice On"
        : " Voice Off";

    button.setAttribute("aria-pressed", String(voiceEnabled));

    if (voiceEnabled) {
        speak("Voice navigation enabled");
    }
}


function readPage() {
    const main = document.getElementById("main-content");

    if (!main) return;

    window.speechSynthesis.cancel();

    const copy = main.cloneNode(true);

    // Remove content that should never be spoken.
    copy.querySelectorAll(
        'input, textarea, select, script, style, ' +
        '[hidden], [aria-hidden="true"], ' +
        '.sr-only, [data-no-speech]'
    ).forEach(element => element.remove());

    const text = copy.innerText || copy.textContent;

    if (!text.trim()) return;

    const message = new SpeechSynthesisUtterance(text.trim());
    message.rate = 0.9;
    message.lang = "en-US";

    window.speechSynthesis.speak(message);
}

function stopReading() {
    clearTimeout(hoverTimer);
    window.speechSynthesis.cancel();
}

